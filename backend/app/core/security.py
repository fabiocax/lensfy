"""Local-only access control for a single-user desktop app.

Lensfy runs on the user's machine; the API and UI must not be reachable from
other machines, and must resist browser-based cross-origin / DNS-rebinding
attacks against ``localhost``. There is intentionally **no login/password** —
instead a random **device token** is generated once and stored on the machine.

Three layers, applied by :class:`LocalSecurityMiddleware` to every HTTP and
WebSocket request:

1. **Loopback enforcement** — the connecting client must be loopback
   (127.0.0.0/8, ::1). Blocks remote machines even if the server is
   accidentally bound to a non-loopback address.
2. **Host-header allowlist** — the ``Host`` must be a loopback name. Blocks
   DNS-rebinding (a remote page resolving its domain to 127.0.0.1).
3. **Device token** on ``/api`` and ``/ws`` — a secret embedded in the served
   UI (loopback-only) and sent back as the ``X-Lensfy-Token`` header (REST) or
   ``token`` query param (WebSocket). A cross-origin attacker can neither read
   the token nor set the custom header, so this also defeats CSRF.

``LENSFY_ALLOW_REMOTE=true`` opts out of (1)+(2) for intentional LAN exposure;
the token (3) still applies. ``LENSFY_SECURITY_ENABLED=false`` disables all of
it (tests / trusted environments).
"""

from __future__ import annotations

import ipaddress
import json
import secrets
from urllib.parse import parse_qs

from app.core.config import Settings, get_settings

TOKEN_HEADER = "x-lensfy-token"
TOKEN_QUERY = "token"
PROTECTED_PREFIXES = ("/api", "/ws")
# Paths under a protected prefix that are exempt from the token gate (still
# loopback + Host protected): the onboarding flow that *provisions* the token.
PUBLIC_API_PREFIXES = ("/api/onboarding",)
# In-process Starlette TestClient sentinels (peer host "testclient", Host header
# "testserver"); never real network values, so safe to treat as local.
_TEST_SENTINELS = ("testclient", "testserver")

_token_cache: str | None = None


def read_device_token() -> str | None:
    """Return the persisted device token, or ``None`` if not provisioned yet.

    Never creates one — provisioning happens explicitly via the onboarding
    screen (:func:`ensure_device_token`), so first run shows onboarding instead
    of a token silently appearing.
    """
    global _token_cache
    if _token_cache:
        return _token_cache
    path = get_settings().data_dir / "device_token"
    if path.exists():
        token = path.read_text().strip()
        if token:
            _token_cache = token
            return token
    return None


def ensure_device_token() -> str:
    """Return the device token, generating + persisting it on first use.

    Stored at ``<data_dir>/device_token`` with ``0600`` perms. Idempotent: a
    second call returns the existing token.
    """
    global _token_cache
    existing = read_device_token()
    if existing:
        return existing
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "device_token"
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    try:
        path.chmod(0o600)
    except OSError:  # best-effort on platforms without POSIX perms
        pass
    _token_cache = token
    return token


def reset_token_cache() -> None:
    """Drop the cached token (used by tests; also after token rotation)."""
    global _token_cache
    _token_cache = None


def _host_only(host_header: str) -> str:
    """Strip the port from a Host header, handling ``[::1]:port`` IPv6 form."""
    h = host_header.strip()
    if h.startswith("["):  # [::1] or [::1]:8000
        return h[1 : h.index("]")] if "]" in h else h
    if h.count(":") == 1:  # host:port
        return h.rsplit(":", 1)[0]
    return h


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost" or host in _TEST_SENTINELS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_allowed(host_header: str | None, settings: Settings) -> bool:
    if not host_header:
        return False  # a missing Host is suspicious; require one
    host = _host_only(host_header)
    if host == "localhost" or host in _TEST_SENTINELS:
        return True
    if host in set(settings.allowed_hosts):
        return True
    return is_loopback(host)


class LocalSecurityMiddleware:
    """ASGI middleware enforcing the local-only access layers (HTTP + WebSocket)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        settings = get_settings()
        if not settings.security_enabled:
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        client = scope.get("client")
        client_host = client[0] if client else None
        path = scope.get("path", "")
        method = scope.get("method", "")

        # (1) loopback + (2) Host allowlist — skipped when remote is opted-in.
        if not settings.allow_remote:
            if not is_loopback(client_host):
                return await self._deny(scope, receive, send, 403, "Acesso remoto bloqueado")
            if not host_allowed(headers.get("host"), settings):
                return await self._deny(scope, receive, send, 403, "Host não permitido")

        # (3) device token on the sensitive surface. CORS preflight (OPTIONS)
        # carries no credentials and only asks "would this be allowed" — exempt
        # it; the real request that follows still needs the token.
        protected = path.startswith(PROTECTED_PREFIXES) and not path.startswith(
            PUBLIC_API_PREFIXES
        )
        if method != "OPTIONS" and protected:
            if not self._token_ok(headers, scope):
                return await self._deny(scope, receive, send, 401, "Token de dispositivo ausente ou inválido")

        return await self.app(scope, receive, send)

    @staticmethod
    def _token_ok(headers: dict[str, str], scope) -> bool:
        expected = read_device_token()
        if not expected:
            return False  # not provisioned yet -> nothing is valid (do onboarding)
        got = headers.get(TOKEN_HEADER)
        if not got:
            qs = parse_qs(scope.get("query_string", b"").decode())
            vals = qs.get(TOKEN_QUERY)
            got = vals[0] if vals else None
        return bool(got) and secrets.compare_digest(got, expected)

    async def _deny(self, scope, receive, send, status: int, detail: str):
        if scope["type"] == "websocket":
            # Consume the connect event, then reject the handshake.
            try:
                await receive()
            except Exception:  # noqa: BLE001
                pass
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": detail}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
