"""TOML ⇄ dataclass helpers shared by the device and server configs.

Every key has a default. Values come from, in order of precedence:
environment variables BIX_<SECTION>_<KEY> > the TOML file > defaults.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, TypeVar

import tomli_w

T = TypeVar("T")


def data_dir(kind: str) -> Path:
    """Default data directory for 'device' or 'server' state."""
    if env := os.environ.get("BIX_DATA_DIR"):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Bix"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Bix"
    elif os.geteuid() == 0:  # system service on the device / a server
        base = Path("/var/lib/bix")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "bix"
    return base / kind


def load(cls: type[T], path: Path, env_prefix: str = "BIX") -> T:
    obj = cls()
    if path.exists():
        with path.open("rb") as f:
            apply(obj, tomllib.load(f))
    apply_env(obj, env_prefix)
    return obj


def save(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(tomli_w.dumps(to_dict(obj)).encode("utf-8"))
    tmp.replace(path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    return obj


def redacted(obj: Any) -> Any:
    """to_dict() with secrets masked, for showing settings in a UI or log."""
    d = to_dict(obj)

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: ("••••" if v and _is_secret(k) else walk(v)) for k, v in x.items()}
        return x

    return walk(d)


def _is_secret(key: str) -> bool:
    k = key.lower()
    return not k.endswith("_env") and any(s in k for s in ("password", "secret", "api_key", "token", "key")) and k not in ("key_env",)


def apply(obj: Any, data: dict[str, Any]) -> None:
    """Merge a (possibly partial) dict into a dataclass tree, coercing types."""
    fields = {f.name: f for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key not in fields:
            continue
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current):
            if isinstance(value, dict):
                apply(current, value)
        else:
            setattr(obj, key, coerce(current, value))


def coerce(current: Any, value: Any) -> Any:
    if isinstance(current, bool):
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, list):
        if isinstance(value, str):
            return [x.strip() for x in value.replace(";", ",").split(",") if x.strip()]
        return list(value)
    if isinstance(current, Path):
        return Path(value)
    return "" if value is None else str(value)


def apply_env(obj: Any, prefix: str) -> None:
    """BIX_STT_PROVIDER=azure sets obj.stt.provider; nested sections join with _."""
    for f in dataclasses.fields(obj):
        name = f"{prefix}_{f.name}".upper()
        current = getattr(obj, f.name)
        if dataclasses.is_dataclass(current):
            apply_env(current, name)
        elif name in os.environ:
            setattr(obj, f.name, coerce(current, os.environ[name]))


def secret(value: str, env_name: str) -> str:
    return value or (os.environ.get(env_name, "") if env_name else "")
