"""Tests for the local-only access control (loopback + device token).

Unit-tests the host/loopback predicates directly (TestClient can't fake a
remote peer), and the token gate through the app with security enabled.
"""

import pytest

from app.core import security
from app.core.config import get_settings


def test_loopback_predicate():
    assert security.is_loopback("127.0.0.1")
    assert security.is_loopback("::1")
    assert security.is_loopback("localhost")
    assert not security.is_loopback("10.0.0.5")
    assert not security.is_loopback("8.8.8.8")
    assert not security.is_loopback(None)


def test_host_allowed():
    s = get_settings()
    s.allowed_hosts = ["mybox"]
    try:
        assert security.host_allowed("127.0.0.1:8000", s)
        assert security.host_allowed("localhost", s)
        assert security.host_allowed("[::1]:8000", s)
        assert security.host_allowed("mybox:8000", s)  # configured extra host
        assert not security.host_allowed("evil.com", s)  # DNS-rebinding target
        assert not security.host_allowed(None, s)
    finally:
        s.allowed_hosts = []


@pytest.fixture
def secure(tmp_path):
    """Enable security with a fresh device token under a temp data dir."""
    s = get_settings()
    prev_enabled, prev_dir = s.security_enabled, s.data_dir
    s.security_enabled = True
    s.data_dir = tmp_path
    security.reset_token_cache()
    token = security.ensure_device_token()
    yield token
    s.security_enabled = prev_enabled
    s.data_dir = prev_dir
    security.reset_token_cache()


def test_api_requires_token(client, secure):
    # No token -> 401.
    assert client.get("/api/clusters").status_code == 401
    # Wrong token -> 401.
    assert client.get("/api/clusters", headers={"X-Lensfy-Token": "nope"}).status_code == 401
    # Correct token -> passes the gate (200 with the empty cluster list).
    ok = client.get("/api/clusters", headers={"X-Lensfy-Token": secure})
    assert ok.status_code == 200


def test_health_is_public(client, secure):
    # /health is outside the protected prefixes (control script probe).
    assert client.get("/health").status_code == 200


def test_ui_shell_needs_no_token(client, secure):
    # The shell must load without a token (it carries the token to the SPA).
    assert client.get("/").status_code == 200


def test_ws_rejected_without_token(client, secure):
    # The handshake is rejected (closed) before reaching the route.
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/watch?cluster_id=1&kind=pods") as ws:
            ws.receive_text()


def test_onboarding_provisions_token(client, tmp_path):
    # Fresh machine: security on, no token yet.
    s = get_settings()
    prev_enabled, prev_dir = s.security_enabled, s.data_dir
    s.security_enabled = True
    s.data_dir = tmp_path
    security.reset_token_cache()
    try:
        # Before onboarding: protected API is blocked, status reports unprovisioned.
        assert client.get("/api/clusters").status_code == 401
        status = client.get("/api/onboarding/status").json()
        assert status["security_enabled"] is True
        assert status["token_provisioned"] is False

        # GET is read-only: returns null and does NOT create a token.
        assert client.get("/api/onboarding/token").json()["token"] is None
        assert not (tmp_path / "device_token").exists()

        # POST is exempt from the gate and provisions the token.
        token = client.post("/api/onboarding/token").json()["token"]
        assert len(token) > 20
        assert (tmp_path / "device_token").read_text().strip() == token

        # Now the token works, GET returns it, and provisioning is idempotent.
        assert client.get("/api/clusters", headers={"X-Lensfy-Token": token}).status_code == 200
        assert client.get("/api/onboarding/token").json()["token"] == token
        assert client.post("/api/onboarding/token").json()["token"] == token
        assert client.get("/api/onboarding/status").json()["token_provisioned"] is True
    finally:
        s.security_enabled = prev_enabled
        s.data_dir = prev_dir
        security.reset_token_cache()


def test_token_is_persisted_and_stable(secure):
    first = security.ensure_device_token()
    security.reset_token_cache()
    second = security.ensure_device_token()  # re-read from disk
    assert first == second and len(first) > 20
