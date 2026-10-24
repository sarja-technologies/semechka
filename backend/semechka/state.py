from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .config import Config
from .crypto import Vault, load_or_create_master_key
from . import migrations
from .db import Database
from .scheduler import Scheduler
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
    scheduler: Scheduler = field(default_factory=Scheduler)  # per-backend generation queue (see scheduler.py)

    @property
    def cookie_name(self) -> str:
        """Per-instance session cookie. Browsers share cookies across ports on one host, so two instances
        on the same address must not use the same name. Derived from this instance's own master key."""
        return "smk_" + self.vault.mac("session-cookie-name", "cookie")[:10]

    @classmethod
    def build(cls, cfg: Config) -> "AppState":
        cfg.ensure()
        db = Database(cfg.db_url)
        db.create_all()
        migrations.apply(db)
        vault = Vault(load_or_create_master_key(cfg.key_path))
        return cls(cfg, db, vault, SettingsService(db, vault))
