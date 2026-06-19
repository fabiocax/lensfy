"""Tests for the update-availability check."""

from app.core import updates
from app.core.config import get_settings


def test_same_ref_matches_short_and_full():
    assert updates._same_ref("abc1234", "abc1234deadbeef", "abc1234") is True
    assert updates._same_ref("abc1234", "different00000", "differe") is False
    assert updates._same_ref(None, "abc1234deadbeef", "abc1234") is False
    assert updates._same_ref("", "abc1234deadbeef", "abc1234") is False


def test_check_update_disabled():
    s = get_settings()
    prev = s.update_check_enabled
    s.update_check_enabled = False
    try:
        out = updates.check_update(force=True)
        assert out == {"enabled": False, "available": False}
    finally:
        s.update_check_enabled = prev


def test_check_update_available(monkeypatch):
    monkeypatch.setattr(updates, "installed_ref", lambda: "0000aaa")
    monkeypatch.setattr(updates, "_latest_remote", lambda repo, branch: {
        "full_sha": "9999bbbcccc", "short_sha": "9999bbb",
        "message": "feat: nova feature", "date": "2026-06-19T00:00:00Z",
        "html_url": "https://github.com/fabiocax/lensfy/commit/9999bbb",
    })
    out = updates.check_update(force=True)
    assert out["available"] is True
    assert out["current"] == "0000aaa" and out["latest"] == "9999bbb"
    assert out["latest_message"] == "feat: nova feature"


def test_check_update_up_to_date(monkeypatch):
    monkeypatch.setattr(updates, "installed_ref", lambda: "abc1234")
    monkeypatch.setattr(updates, "_latest_remote", lambda repo, branch: {
        "full_sha": "abc1234deadbeef", "short_sha": "abc1234",
        "message": "x", "date": None, "html_url": None,
    })
    out = updates.check_update(force=True)
    assert out["available"] is False
    assert out["checked"] is True


def test_check_update_network_error_is_soft(monkeypatch):
    def boom(repo, branch):
        raise RuntimeError("offline")

    monkeypatch.setattr(updates, "installed_ref", lambda: "abc1234")
    monkeypatch.setattr(updates, "_latest_remote", boom)
    out = updates.check_update(force=True)
    assert out["available"] is False and out["checked"] is False
    assert "offline" in out["error"]


def test_update_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(updates, "installed_ref", lambda: "0000aaa")
    monkeypatch.setattr(updates, "_latest_remote", lambda repo, branch: {
        "full_sha": "9999bbbcccc", "short_sha": "9999bbb",
        "message": "nova", "date": None, "html_url": "http://x",
    })
    resp = client.get("/api/update/status?force=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True and body["latest"] == "9999bbb"
