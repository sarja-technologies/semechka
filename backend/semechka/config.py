"""Process-level configuration (environment only). Product settings live in the settings registry."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _data_dir() -> Path:
    return Path(os.environ.get("SEMECHKA_DATA_DIR", Path.home() / ".local/share/semechka")).expanduser()


@dataclass
class Config:
    data_dir: Path = field(default_factory=_data_dir)
    database_url: str | None = field(default_factory=lambda: os.environ.get("SEMECHKA_DATABASE_URL"))
    # Private by default: loopback only. LAN exposure is an explicit choice.
    host: str = field(default_factory=lambda: os.environ.get("SEMECHKA_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("SEMECHKA_PORT", "8780")))
    secure_cookies: bool = field(default_factory=lambda: os.environ.get("SEMECHKA_SECURE_COOKIES", "auto") == "1")
    cookie_mode: str = field(default_factory=lambda: os.environ.get("SEMECHKA_SECURE_COOKIES", "auto"))
    web_dir: Path | None = field(default_factory=lambda: Path(p) if (p := os.environ.get("SEMECHKA_WEB_DIR")) else None)
    # Test hooks: disable background sweepers.
    background: bool = True

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'semechka.db'}"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "master.key"

    @property
    def setup_token_path(self) -> Path:
        return self.data_dir / "setup-token"
