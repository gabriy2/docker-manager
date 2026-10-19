"""Configurazione applicativa, tutta via variabili d'ambiente."""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        self.app_name: str = os.getenv("APP_NAME", "Docker Manager")
        self.data_dir: Path = Path(os.getenv("DATA_DIR", "/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path: Path = self.data_dir / "docker-manager.db"
        self.db_url: str = f"sqlite+pysqlite:///{self.db_path}"

        # Docker
        self.docker_host: str | None = os.getenv("DOCKER_HOST") or None
        self.stop_timeout: int = _env_int("STOP_TIMEOUT", 10)
        self.compose_cli: bool = _env_bool("COMPOSE_CLI", False)
        self.hide_self: bool = _env_bool("HIDE_SELF", False)

        # Metriche live (CPU/RAM). Disattivabile su host con molti container.
        self.enable_stats: bool = _env_bool("ENABLE_STATS", True)
        self.stats_interval: int = max(3, _env_int("STATS_INTERVAL", 8))
        self.stats_workers: int = max(1, _env_int("STATS_WORKERS", 8))

        # Scheduling
        self.timezone_name: str = os.getenv("TZ", "Europe/Rome")
        try:
            self.timezone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            self.timezone_name = "UTC"
            self.timezone = ZoneInfo("UTC")

        # Auth (opzionale: se AUTH_PASSWORD è vuoto l'app è aperta)
        self.auth_user: str = os.getenv("AUTH_USER", "admin")
        self.auth_password: str = os.getenv("AUTH_PASSWORD", "")
        self.session_days: int = _env_int("SESSION_DAYS", 14)
        self.secret_key: str = os.getenv("SECRET_KEY", "") or self._persisted_secret()

        # Audit
        self.log_retention: int = _env_int("LOG_RETENTION", 500)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_password)

    def _persisted_secret(self) -> str:
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            value = key_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_urlsafe(48)
        key_file.write_text(value, encoding="utf-8")
        with contextlib.suppress(OSError):
            key_file.chmod(0o600)
        return value


settings = Settings()
