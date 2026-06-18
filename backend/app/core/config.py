from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via env vars prefixed ``LENSFY_``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LENSFY_",
        extra="ignore",
    )

    app_name: str = "Lensfy Backend"
    version: str = "0.1.0"
    debug: bool = False

    api_prefix: str = "/api"

    # Where local state (SQLite db, audit, history) lives.
    data_dir: Path = Path.home() / ".lensfy"
    # Explicit override; when empty a sqlite file under ``data_dir`` is used.
    database_url: str = ""

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:1420",
        "tauri://localhost",
    ]

    # Local-only access control (see app.core.security). Defaults are safe for a
    # single-user desktop: loopback-only + device token on /api and /ws.
    security_enabled: bool = True
    # Opt in to non-loopback (LAN) access; the device token still applies.
    allow_remote: bool = False
    # Extra Host-header values to accept (e.g. a machine hostname) when not
    # allowing remote — loopback names are always accepted.
    allowed_hosts: list[str] = []

    # AI assistant (Claude API). Empty key disables the feature (degrades in UI).
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_base_url: str = "https://api.anthropic.com"
    # Allow the agent to run mutating actions (each still needs UI approval).
    ai_allow_mutations: bool = True

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'lensfy.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
