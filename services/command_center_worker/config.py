"""Configuration for the PulseSoc Command Center worker skeleton."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_SERVICE_NAME = "command-center-worker"
DEFAULT_SERVICE_ROLE = "worker"
DEFAULT_HEARTBEAT_SECONDS = 30
MIN_HEARTBEAT_SECONDS = 5
MAX_HEARTBEAT_SECONDS = 3600
LOCAL_ENV_FILES_LOADED: list[str] = []


def _load_local_env_file(path: Path) -> None:
    if not path.exists():
        return
    loaded_any = False
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value
                loaded_any = True
    if loaded_any:
        LOCAL_ENV_FILES_LOADED.append(str(path))


def load_local_environment() -> None:
    if os.getenv("COINPILOTX_DISABLE_LOCAL_ENV", "").strip().lower() in TRUE_VALUES:
        return
    root = Path(__file__).resolve().parents[2]
    _load_local_env_file(root / ".env.local")
    _load_local_env_file(root / ".env")


load_local_environment()


def env_text(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else default


def env_bool(key: str, default: bool = False) -> bool:
    if key in os.environ:
        return env_text(key).lower() in TRUE_VALUES
    return default


def env_int(key: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = env_text(key)
    try:
        value = int(raw_value) if raw_value else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def environment_name() -> str:
    return (
        env_text("RAILWAY_ENVIRONMENT")
        or env_text("ENV")
        or env_text("FLASK_ENV")
        or env_text("APP_ENV")
        or "local"
    )


@dataclass(frozen=True)
class WorkerConfig:
    service_name: str
    service_role: str
    worker_enabled: bool
    internal_token_configured: bool
    database_url: str
    redis_url: str
    heartbeat_seconds: int
    environment: str
    version: str

    @property
    def redis_configured(self) -> bool:
        return bool(self.redis_url)

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)


def load_config() -> WorkerConfig:
    return WorkerConfig(
        service_name=env_text("PULSESOC_SERVICE_NAME", DEFAULT_SERVICE_NAME) or DEFAULT_SERVICE_NAME,
        service_role=env_text("PULSESOC_SERVICE_ROLE", DEFAULT_SERVICE_ROLE) or DEFAULT_SERVICE_ROLE,
        worker_enabled=env_bool("COMMAND_CENTER_WORKER_ENABLED", True),
        internal_token_configured=bool(env_text("COMMAND_CENTER_INTERNAL_TOKEN")),
        database_url=env_text("DATABASE_URL"),
        redis_url=env_text("REDIS_URL"),
        heartbeat_seconds=env_int(
            "COMMAND_CENTER_HEARTBEAT_SECONDS",
            DEFAULT_HEARTBEAT_SECONDS,
            MIN_HEARTBEAT_SECONDS,
            MAX_HEARTBEAT_SECONDS,
        ),
        environment=environment_name(),
        version=env_text("RELEASE_VERSION") or env_text("RAILWAY_GIT_COMMIT_SHA")[:12],
    )
