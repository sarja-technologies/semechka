from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .config import Config
from .crypto import Vault, load_or_create_master_key
from .db import Database
from .security import RateLimiter
from .settings_svc import SettingsService


@dataclass
class AppState:
    cfg: Config
    db: Database
    vault: Vault
    settings: SettingsService
    limiter: RateLimiter = field(default_factory=RateLimiter)
    active: dict[str, int] = field(default_factory=dict)  # user_id -> in-flight generations
    health_cache: dict[str, tuple[float, dict]] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    stop_events: dict[str, asyncio.Event] = field(default_factory=dict)

    @classmethod
    def build(cls, cfg: Config) -> "AppState":
        cfg.ensure()
        db = Database(cfg.db_url)
        db.create_all()
        vault = Vault(load_or_create_master_key(cfg.key_path))
        return cls(cfg, db, vault, SettingsService(db, vault))
