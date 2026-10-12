"""Object storage for audio, transcripts and exports: local disk or S3 (SSE-KMS)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

from bix.server.config import ServerConfig

_KEY = re.compile(r"^[\w\-./]+$")


def _check(key: str) -> str:
    if not _KEY.match(key) or ".." in key or key.startswith("/"):
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def local_path(self, key: str) -> Path | None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        return self.root / _check(key)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        import shutil

        p = self._p(prefix.rstrip("/"))
        if p.is_dir():
            n = sum(1 for _ in p.rglob("*") if _.is_file())
            shutil.rmtree(p)
            return n
        if p.exists():
            p.unlink()
            return 1
        return 0

    def local_path(self, key: str) -> Path | None:
        p = self._p(key)
        return p if p.exists() else None


class S3Storage:
    def __init__(self, cfg: ServerConfig) -> None:
        import boto3

        s = cfg.storage
        self.bucket = s.s3_bucket
        self.prefix = s.s3_prefix
        self.kms = s.s3_kms_key_id
        self.client = boto3.client("s3", region_name=s.s3_region, endpoint_url=s.s3_endpoint_url or None)

    def _k(self, key: str) -> str:
        return self.prefix + _check(key)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms} if self.kms else {"ServerSideEncryption": "AES256"}
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=content_type, **extra)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except self.client.exceptions.ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
                n += len(objs)
        return n

    def local_path(self, key: str) -> Path | None:
        return None


class AzureBlobStorage:
    """Azure Blob Storage. Login: the connection string (account key) from an environment
    variable, or Entra ID (the app or managed identity from [azure], role
    "Storage Blob Data Contributor" on the container). Encryption at rest is Azure's
    (with a customer-managed key if the storage account is set up that way)."""

    def __init__(self, cfg: ServerConfig) -> None:
        import os

        from azure.storage.blob import BlobServiceClient

        s = cfg.storage
        self.prefix = s.azure_prefix
        conn = os.environ.get(s.azure_connection_string_env, "")
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
        elif s.azure_account_url:
            svc = BlobServiceClient(s.azure_account_url, credential=_EntraCredential(cfg.azure))
        else:
            raise RuntimeError(f"Azure Blob: задайте storage.azure_account_url или {s.azure_connection_string_env}.")
        self.container = svc.get_container_client(s.azure_container)

    def _k(self, key: str) -> str:
        return self.prefix + _check(key)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(self._k(key), data, overwrite=True, content_settings=ContentSettings(content_type=content_type))

    def get(self, key: str) -> bytes:
        return self.container.download_blob(self._k(key)).readall()

    def exists(self, key: str) -> bool:
        return self.container.get_blob_client(self._k(key)).exists()

    def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.container.delete_blob(self._k(key), delete_snapshots="include")
        except ResourceNotFoundError:
            pass

    def delete_prefix(self, prefix: str) -> int:
        names = [b.name for b in self.container.list_blobs(name_starts_with=self._k(prefix))]
        for i in range(0, len(names), 256):  # batch delete takes up to 256 blobs
            self.container.delete_blobs(*names[i:i + 256], delete_snapshots="include")
        return len(names)

    def local_path(self, key: str) -> Path | None:
        return None

    def check(self) -> str:
        self.container.get_container_properties()
        return self.container.url


class _EntraCredential:
    """azure-core TokenCredential on top of our own Entra client (no azure-identity needed)."""

    def __init__(self, azure_cfg) -> None:
        from bix.server.azure_auth import EntraToken

        self._tok = EntraToken(azure_cfg, "https://storage.azure.com/.default")

    def get_token(self, *scopes, **kwargs):
        import time

        from azure.core.credentials import AccessToken

        token = self._tok.get()
        return AccessToken(token, int(self._tok._tok[1]) if self._tok._tok else int(time.time()) + 600)


def make(cfg: ServerConfig) -> Storage:
    if cfg.storage.backend == "s3":
        return S3Storage(cfg)
    if cfg.storage.backend == "azure_blob":
        return AzureBlobStorage(cfg)
    return LocalStorage(cfg.storage_dir)
