import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # ``NoDecode`` so a plain comma-separated env value (the natural form, e.g.
    # ``LENSFY_CORS_ORIGINS=a,b``) is accepted by the validator below instead of
    # pydantic-settings trying to JSON-decode it and hard-crashing on startup.
    cors_origins: Annotated[list[str], NoDecode] = [
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
    allowed_hosts: Annotated[list[str], NoDecode] = []

    # AI assistant (Claude API). Empty key disables the feature (degrades in UI).
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_base_url: str = "https://api.anthropic.com"
    # Allow the agent to run mutating actions (each still needs UI approval).
    # Opt-in: a destructive capability must not be on by default.
    ai_allow_mutations: bool = False

    # Update check: compares the installed git ref against the latest commit on
    # GitHub so the UI can show an "update available" notice. Best-effort and
    # cached; disable to avoid the outbound call to api.github.com.
    update_check_enabled: bool = True
    update_repo: str = "fabiocax/lensfy"  # owner/repo on GitHub
    update_branch: str = "main"

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_list(cls, v):
        """Accept both JSON (``["a","b"]``) and comma-separated (``a,b``) env values."""
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'lensfy.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
