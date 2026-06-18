"""Cluster CRUD tests. The Kubernetes layer is patched so no live cluster or
kubeconfig is required.
"""

import pytest

from app.kubernetes.client import ContextInfo
from app.services import cluster as cluster_service


@pytest.fixture(autouse=True)
def fake_kubernetes(monkeypatch):
    """Stub context discovery and the live-cluster probe."""
    monkeypatch.setattr(
        cluster_service,
        "contexts_from_kubeconfig",
        lambda path=None, content=None: [
            ContextInfo(name="kind-dev", cluster="kind-dev", server=None),
            ContextInfo(name="prod-eks", cluster="prod-eks-cluster", server=None),
        ],
    )

    class _FakeClient:
        def __init__(self, context, kubeconfig_path=None, insecure=False):
            self.context = context

        def server_version(self):
            return "v1.30.0"

    monkeypatch.setattr(cluster_service, "KubernetesClient", _FakeClient)


def test_list_clusters_empty(client):
    assert client.get("/api/clusters").json() == []


def test_import_clusters_detects_contexts_and_provider(client):
    resp = client.post("/api/clusters", json={"kubeconfig_path": "/tmp/kubeconfig"})
    assert resp.status_code == 201
    body = resp.json()
    assert {c["context"] for c in body} == {"kind-dev", "prod-eks"}

    providers = {c["context"]: c["provider"] for c in body}
    assert providers["kind-dev"] == "kind"
    assert providers["prod-eks"] == "aws"
    # Import returns immediately ("unknown"); status/version come from a later refresh.
    assert all(c["status"] == "unknown" for c in body)
    assert all(c["version"] is None for c in body)


def test_refresh_populates_status(client):
    created = client.post(
        "/api/clusters", json={"kubeconfig_path": "/tmp/kubeconfig", "context": "kind-dev"}
    ).json()[0]
    refreshed = client.post(f"/api/clusters/{created['id']}/refresh").json()
    assert refreshed["status"] == "connected"
    assert refreshed["version"] == "v1.30.0"


def test_detect_contexts(client):
    resp = client.post("/api/clusters/contexts", json={"kubeconfig_path": "/tmp/kubeconfig"})
    assert resp.status_code == 200
    assert {c["name"] for c in resp.json()} == {"kind-dev", "prod-eks"}


def test_import_selected_contexts(client):
    resp = client.post(
        "/api/clusters",
        json={"kubeconfig_path": "/tmp/kubeconfig", "contexts": ["prod-eks"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body) == 1 and body[0]["context"] == "prod-eks"


def test_import_single_context(client):
    resp = client.post(
        "/api/clusters",
        json={"kubeconfig_path": "/tmp/kubeconfig", "context": "kind-dev"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body) == 1
    assert body[0]["context"] == "kind-dev"


def test_reimport_updates_in_place(client):
    first = client.post("/api/clusters", json={"kubeconfig_path": "/tmp/kubeconfig"}).json()
    # Re-importing updates existing contexts (e.g. toggling insecure) without duplicating.
    resp = client.post(
        "/api/clusters",
        json={"kubeconfig_path": "/tmp/kubeconfig", "insecure": True},
    )
    assert resp.status_code == 201
    again = resp.json()
    assert {c["id"] for c in again} == {c["id"] for c in first}  # same rows, not new
    assert all(c["insecure"] for c in again)
    assert len(client.get("/api/clusters").json()) == len(first)  # no duplicates


def test_get_update_delete_cluster(client):
    created = client.post(
        "/api/clusters",
        json={"kubeconfig_path": "/tmp/kubeconfig", "context": "kind-dev"},
    ).json()[0]
    cid = created["id"]

    assert client.get(f"/api/clusters/{cid}").json()["context"] == "kind-dev"

    patched = client.patch(
        f"/api/clusters/{cid}", json={"name": "Local Dev", "favorite": True}
    ).json()
    assert patched["name"] == "Local Dev"
    assert patched["favorite"] is True

    assert client.delete(f"/api/clusters/{cid}").status_code == 204
    assert client.get(f"/api/clusters/{cid}").status_code == 404


def test_get_missing_cluster_returns_404(client):
    assert client.get("/api/clusters/999").status_code == 404
