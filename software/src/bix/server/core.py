"""Server context shared by the HTTP API, the worker and the CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from bix.common.events import Bus
from bix.server.calendars import Calendars
from bix.server import storage as storage_mod
from bix.server.config import ServerConfig
from bix.server.db import Database
from bix.server.integrations import Graph

log = logging.getLogger(__name__)


@dataclass
class Server:
    cfg: ServerConfig
    cfg_path: Path
    db: Database
    storage: storage_mod.Storage
    graph: Graph
    bus: Bus = field(default_factory=Bus)
    calendars: Calendars = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calendars is None:
            self.calendars = Calendars(self.graph)

    @classmethod
    def create(cls, cfg: ServerConfig, cfg_path: Path) -> "Server":
        cfg.dir.mkdir(parents=True, exist_ok=True)
        db = Database(cfg.db_url)
        db.create_all()
        from bix.server import minutes

        minutes.use_azure_identity(cfg.azure)  # tokens are fetched only if a provider uses Entra ID
        return cls(cfg=cfg, cfg_path=cfg_path, db=db, storage=storage_mod.make(cfg), graph=Graph(cfg.graph, cfg.azure))


def key(org_id: str, meeting_id: str, name: str) -> str:
    return f"orgs/{org_id}/meetings/{meeting_id}/{name}"


def prefix(org_id: str, meeting_id: str) -> str:
    return f"orgs/{org_id}/meetings/{meeting_id}/"
