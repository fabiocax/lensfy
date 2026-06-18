"""First-run onboarding: provision the device token (no login/password).

These endpoints are exempt from the token gate (see
``security.PUBLIC_API_PREFIXES``) but remain loopback + Host protected, so only
a local client on this machine can provision the token.
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.security import ensure_device_token, read_device_token

router = APIRouter()


@router.get("/status")
def onboarding_status():
    """Whether auth is on and whether the device token has been provisioned."""
    return {
        "security_enabled": get_settings().security_enabled,
        "token_provisioned": read_device_token() is not None,
    }


@router.get("/token")
def current_token():
    """Return the existing device token (or null) **without** generating one.

    The SPA fetches this at startup so the token is never embedded in (and
    therefore never cached with) the HTML shell — a stale PWA cache would
    otherwise serve an empty/outdated token. Loopback + Host gated like all
    onboarding routes; a local client could read the token file anyway.
    """
    return {"token": read_device_token()}


@router.post("/token")
def provision_token():
    """Generate (or return the existing) device token for this machine."""
    return {"token": ensure_device_token()}
