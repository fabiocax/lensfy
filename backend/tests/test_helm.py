"""Helm release endpoint: graceful when the binary is missing, lists when present."""

from app.kubernetes import helm as helm_mod
from app.models.cluster import Cluster


def test_releases_unavailable_when_helm_missing(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    monkeypatch.setattr(helm_mod, "available", lambda: False)
    resp = client.get("/api/helm/releases?cluster_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["releases"] == []
    assert "helm" in body["message"].lower()


def test_releases_lists_when_helm_present(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    monkeypatch.setattr(helm_mod, "available", lambda: True)
    monkeypatch.setattr(
        helm_mod,
        "list_releases",
        lambda cluster: [{"name": "argocd", "namespace": "argocd", "chart": "argo-cd-5.0.0",
                          "revision": "1", "status": "deployed"}],
    )
    resp = client.get("/api/helm/releases?cluster_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["releases"][0]["name"] == "argocd"
