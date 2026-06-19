"""Update check: is a newer Lensfy available?

Compares the installed git ref (``$PREFIX/source_ref`` when installed, or
``git rev-parse --short HEAD`` when running from source) against the latest
commit on the configured GitHub branch. Best-effort and cached — a failure
(offline, rate limit) never raises; it just reports ``available: false``.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_TTL = 3600.0  # seconds — don't hammer the GitHub API on every page load
_cache: dict = {"ts": 0.0, "data": None}


def installed_ref() -> str | None:
    """Current installed git ref: the ``source_ref`` written by install.sh, or
    the working-tree HEAD when running from a checkout (dev)."""
    prefix = os.environ.get("LENSFY_PREFIX") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"),
        "lensfy",
    )
    ref_file = Path(prefix) / "source_ref"
    try:
        if ref_file.is_file():
            ref = ref_file.read_text(encoding="utf-8").strip()
            if ref:
                return ref
    except OSError:
        pass
    # Dev fallback: short HEAD of the repo this file lives in.
    try:
        root = Path(__file__).resolve().parents[3]  # backend/app/core/updates.py -> repo
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 - git missing / not a repo
        pass
    return None


def _same_ref(current: str | None, latest_full: str, latest_short: str) -> bool:
    """True when ``current`` points at the same commit as latest (handles the
    short-vs-full SHA mismatch in either direction)."""
    if not current or not (latest_full or latest_short):
        return False
    return latest_full.startswith(current) or current.startswith(latest_short)


def _latest_remote(repo: str, branch: str) -> dict:
    """Latest commit on ``repo``@``branch`` via the GitHub API."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    with httpx.Client(
        timeout=6.0,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "lensfy-update-check"},
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    sha = data.get("sha", "") or ""
    commit = data.get("commit", {}) or {}
    return {
        "full_sha": sha,
        "short_sha": sha[:7],
        "message": (commit.get("message") or "").splitlines()[0][:140] if commit.get("message") else "",
        "date": (commit.get("committer") or {}).get("date"),
        "html_url": data.get("html_url"),
    }


def check_update(force: bool = False) -> dict:
    """Return ``{enabled, available, current, latest, …}``. Cached ~1h; never raises."""
    settings = get_settings()
    if not settings.update_check_enabled:
        return {"enabled": False, "available": False}

    now = time.monotonic()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    current = installed_ref()
    result: dict = {
        "enabled": True, "available": False, "current": current,
        "repo": settings.update_repo, "branch": settings.update_branch,
    }
    try:
        latest = _latest_remote(settings.update_repo, settings.update_branch)
        same = _same_ref(current, latest["full_sha"], latest["short_sha"])
        result.update({
            "checked": True,
            "latest": latest["short_sha"],
            "latest_message": latest["message"],
            "latest_date": latest["date"],
            "latest_url": latest["html_url"],
            "available": bool(current) and bool(latest["short_sha"]) and not same,
        })
    except Exception as exc:  # noqa: BLE001 - offline / rate-limited / parse error
        logger.info("update check failed: %s", exc)
        result.update({"checked": False, "error": str(exc)})

    _cache["ts"] = now
    _cache["data"] = result
    return result
