"""Tests for the generic Explorer resource registry and endpoint.

Field extraction is unit-tested against fake SDK objects (SimpleNamespace);
the endpoint wiring is tested with a patched Kubernetes client.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.kubernetes import resources as R
from app.models.cluster import Cluster
from app.services import workloads as workloads_service


def test_registry_is_wellformed():
    assert R.RESOURCE_KINDS
    for kind, rdef in R.RESOURCES.items():
        assert rdef.columns, kind
        assert callable(rdef.list_fn) and callable(rdef.row_fn), kind


def test_age_formatting():
    now = datetime.now(timezone.utc)
    assert R.age(None) == "-"
    assert R.age(now - timedelta(seconds=30)).endswith("s")
    assert R.age(now - timedelta(minutes=5)) == "5m"
    assert R.age(now - timedelta(hours=3)) == "3h"
    assert R.age(now - timedelta(days=2)) == "2d"


def test_service_row_extraction():
    svc = NS(
        metadata=NS(name="api", namespace="default", creation_timestamp=None),
        spec=NS(
            type="ClusterIP",
            cluster_ip="10.0.0.1",
            ports=[NS(port=80, node_port=None, protocol="TCP")],
        ),
    )
    row = R.RESOURCES["services"].row_fn(svc)
    assert row["name"] == "api"
    assert row["type"] == "ClusterIP"
    assert row["ports"] == "80/TCP"


def test_node_row_extraction():
    node = NS(
        metadata=NS(
            name="cp-1",
            labels={"node-role.kubernetes.io/control-plane": ""},
            creation_timestamp=None,
        ),
        status=NS(
            conditions=[NS(type="Ready", status="True")],
            node_info=NS(kubelet_version="v1.30.0", os_image="Ubuntu 22.04"),
            capacity={"cpu": "4", "memory": "16310152Ki"},
        ),
    )
    row = R.RESOURCES["nodes"].row_fn(node)
    assert row["status"] == "Ready"
    assert row["version"] == "v1.30.0"
    assert "control-plane" in row["roles"]
    assert row["cpu"] == "4"
    assert row["os"] == "Ubuntu 22.04"
    assert row["memory"].endswith("Gi")


def test_unknown_kind_returns_404(client, db_session):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    resp = client.get("/api/resources?cluster_id=1&kind=bogus")
    assert resp.status_code == 404


def test_manifest_unknown_kind_404(client, db_session):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    resp = client.get("/api/resources/manifest?cluster_id=1&kind=bogus&name=x")
    assert resp.status_code == 404


def test_manifest_get_and_apply(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_manifest(self, kind, name, namespace):
            return f"kind: Pod\nmetadata:\n  name: {name}\n"

        def apply_manifest(self, kind, name, namespace, yaml_text):
            return yaml_text  # echo back

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))

    got = client.get("/api/resources/manifest?cluster_id=1&kind=pods&namespace=default&name=p1")
    assert got.status_code == 200
    assert "name: p1" in got.json()["yaml"]

    applied = client.put(
        "/api/resources/manifest?cluster_id=1&kind=pods&namespace=default&name=p1",
        json={"yaml": "kind: Pod\nmetadata:\n  name: p1\n"},
    )
    assert applied.status_code == 200
    assert "name: p1" in applied.json()["yaml"]


def test_manifest_versions_dedup_prune_and_endpoints(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def apply_manifest(self, kind, name, namespace, yaml_text):
            return yaml_text  # echo back

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))

    base = "/api/resources/manifest?cluster_id=1&kind=pods&namespace=default&name=p1"

    def apply(body):
        return client.put(base, json={"yaml": body})

    # 7 distinct applies + one re-apply of the latest (deduped).
    for i in range(7):
        assert apply(f"kind: Pod\n# rev {i}\n").status_code == 200
    assert apply("kind: Pod\n# rev 6\n").status_code == 200  # identical → no new version

    versions = client.get("/api/resources/manifest/versions?cluster_id=1&kind=pods&namespace=default&name=p1")
    assert versions.status_code == 200
    rows = versions.json()
    assert len(rows) == 5  # pruned to MAX_VERSIONS

    # Newest first; the full body is fetchable per version.
    one = client.get(f"/api/resources/manifest/versions/{rows[0]['id']}")
    assert one.status_code == 200
    assert "# rev 6" in one.json()["yaml"]

    # Delete a version.
    assert client.delete(f"/api/resources/manifest/versions/{rows[0]['id']}").status_code == 204
    after = client.get("/api/resources/manifest/versions?cluster_id=1&kind=pods&namespace=default&name=p1")
    assert len(after.json()) == 4


def test_deploy_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def deploy_manifests(self, yaml_text, default_namespace):
            return [
                {"kind": "ConfigMap", "name": "a", "namespace": default_namespace, "status": "created"},
                {"kind": "Service", "name": "b", "namespace": default_namespace,
                 "status": "error", "message": "already exists"},
            ]

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.post(
        "/api/resources/deploy?cluster_id=1",
        json={"yaml": "kind: ConfigMap", "namespace": "team-a"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["created", "error"]
    assert results[0]["namespace"] == "team-a"


def test_validate_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def validate_manifests(self, yaml_text, default_namespace):
            return [
                {"kind": "ConfigMap", "name": "ok", "namespace": default_namespace, "status": "valid"},
                {"kind": "Deployment", "name": "bad", "namespace": default_namespace,
                 "status": "error", "message": "schema inválido"},
            ]

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.post(
        "/api/resources/validate?cluster_id=1",
        json={"yaml": "kind: ConfigMap", "namespace": "default"},
    )
    assert resp.status_code == 200
    statuses = [r["status"] for r in resp.json()["results"]]
    assert statuses == ["valid", "error"]


def test_detail_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_object(self, kind, name, namespace):
            return {"kind": "Pod", "metadata": {"name": name, "namespace": namespace}}

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    assert client.get("/api/resources/detail?cluster_id=1&kind=bogus&name=x").status_code == 404
    resp = client.get("/api/resources/detail?cluster_id=1&kind=pods&name=p1&namespace=default")
    assert resp.status_code == 200
    assert resp.json()["object"]["metadata"]["name"] == "p1"


def test_list_resource_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def list_resource(self, kind, namespace):
            return {
                "kind": kind,
                "namespaced": True,
                "columns": [{"key": "name", "label": "Nome"}],
                "rows": [{"name": "demo"}],
            }

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.get("/api/resources?cluster_id=1&kind=services")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "services"
    assert body["rows"] == [{"name": "demo"}]


def test_node_cordon_and_drain_endpoints(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def cordon_node(self, name, unschedulable):
            calls.append(("cordon", name, unschedulable))

        def drain_node(self, name, grace_period=None):
            calls.append(("drain", name, grace_period))
            return {"cordoned": True, "evicted": 3, "skipped": [], "total": 3}

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    r1 = client.post("/api/resources/node/cordon?cluster_id=1&name=n1&unschedulable=true")
    assert r1.status_code == 200 and r1.json()["status"] == "cordoned"
    r2 = client.post("/api/resources/node/cordon?cluster_id=1&name=n1&unschedulable=false")
    assert r2.json()["status"] == "uncordoned"
    r3 = client.post("/api/resources/node/drain?cluster_id=1&name=n1")
    assert r3.status_code == 200 and r3.json()["evicted"] == 3
    assert ("cordon", "n1", True) in calls and ("drain", "n1", None) in calls


def test_rollout_endpoints(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def rollout_history(self, kind, name, namespace):
            return [{"revision": 2, "name": "rs2", "images": ["nginx:2"], "current": True}]

        def rollout_undo(self, kind, name, namespace, revision):
            assert revision == 1

        def rollout_pause(self, kind, name, namespace, paused):
            assert paused is True

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    h = client.get("/api/resources/rollout/history?cluster_id=1&kind=deployments&name=d&namespace=default")
    assert h.status_code == 200 and h.json()["revisions"][0]["revision"] == 2
    u = client.post("/api/resources/rollout/undo?cluster_id=1&kind=deployments&name=d&namespace=default&revision=1")
    assert u.status_code == 200 and u.json()["revision"] == 1
    p = client.post("/api/resources/rollout/pause?cluster_id=1&kind=deployments&name=d&namespace=default&paused=true")
    assert p.status_code == 200 and p.json()["status"] == "paused"


def test_issues_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def cluster_issues(self):
            return {
                "issues": [{"severity": "critical", "category": "Pods", "kind": "pods",
                            "name": "p1", "namespace": "default", "reason": "CrashLoopBackOff",
                            "detail": "app"}],
                "counts": {"critical": 1, "warning": 0},
                "total": 1,
            }

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.get("/api/metrics/issues?cluster_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and body["issues"][0]["reason"] == "CrashLoopBackOff"


def test_apply_manifest_retries_on_version_conflict():
    """A stale resourceVersion in the edited YAML must not make save flaky:
    apply realigns to the live version and retries on 409."""
    from kubernetes.client.exceptions import ApiException
    from app.kubernetes.client import KubernetesClient

    class FakeObj:
        def __init__(self, d): self._d = d
        def to_dict(self): return self._d

    class FakeRes:
        def __init__(self): self.get_calls = 0; self.replace_calls = 0; self.last_body = None
        def get(self, name, namespace=None):
            self.get_calls += 1
            # versão viva muda a cada leitura (controllers mexendo no objeto)
            return FakeObj({"metadata": {"name": name, "resourceVersion": f"v{self.get_calls}"}})
        def replace(self, body, name, namespace=None):
            self.replace_calls += 1
            self.last_body = body
            if self.replace_calls == 1:
                raise ApiException(status=409, reason="Conflict")  # corrida na 1ª
            return FakeObj({**body, "metadata": {**body["metadata"], "resourceVersion": "final"}})

    res = FakeRes()
    kc = object.__new__(KubernetesClient)
    kc._dynamic = NS(resources=NS(get=lambda api_version, kind: res))

    yaml_text = (
        "apiVersion: v1\nkind: ConfigMap\n"
        "metadata:\n  name: cm1\n  namespace: default\n  resourceVersion: '1'\n"  # obsoleto
        "data:\n  k: v\n"
    )
    out = kc.apply_manifest("configmaps", "cm1", "default", yaml_text)

    assert res.replace_calls == 2 and res.get_calls == 2  # 1 falha + 1 sucesso, re-fetch a cada
    assert res.last_body["metadata"]["resourceVersion"] == "v2"  # realinhado ao vivo
    assert "resourceVersion: final" in out


def test_apply_manifest_raises_on_persistent_conflict():
    from kubernetes.client.exceptions import ApiException
    from app.kubernetes.client import KubernetesClient
    from app.kubernetes.client import KubernetesError

    class FakeObj:
        def __init__(self, d): self._d = d
        def to_dict(self): return self._d

    class FakeRes:
        def get(self, name, namespace=None):
            return FakeObj({"metadata": {"name": name, "resourceVersion": "x"}})
        def replace(self, body, name, namespace=None):
            raise ApiException(status=409, reason="Conflict")  # sempre conflita

    kc = object.__new__(KubernetesClient)
    kc._dynamic = NS(resources=NS(get=lambda api_version, kind: FakeRes()))
    import pytest
    with pytest.raises(KubernetesError):
        kc.apply_manifest("configmaps", "cm1", "default", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cm1\n")


def test_pod_summary_includes_up():
    from datetime import timedelta
    from app.kubernetes.client import KubernetesClient
    now = datetime.now(timezone.utc)
    pod = NS(
        metadata=NS(name="p", namespace="default", creation_timestamp=now - timedelta(days=2)),
        status=NS(container_statuses=[NS(ready=True, restart_count=0)], phase="Running",
                  start_time=now - timedelta(hours=5)),
        spec=NS(node_name="n1", containers=[NS(name="app")]),
    )
    s = KubernetesClient._pod_summary(pod)
    assert s.up == "5h"  # uptime desde start_time, não creation
    assert s.ready == "1/1"


def test_node_ready_shows_cordon():
    node = NS(
        metadata=NS(name="n", labels={}, creation_timestamp=None),
        status=NS(conditions=[NS(type="Ready", status="True")]),
        spec=NS(unschedulable=True),
    )
    assert R._node_ready(node) == "Ready,SchedulingDisabled"


def test_container_resources_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def set_container_resources(self, kind, name, namespace, container, requests, limits):
            seen.update(kind=kind, name=name, container=container, requests=requests, limits=limits)

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.post(
        "/api/resources/container-resources?cluster_id=1&kind=deployments&name=web&namespace=default",
        json={"container": "app", "requests": {"cpu": "100m", "memory": "128Mi"},
              "limits": {"cpu": "500m", "memory": "256Mi"}},
    )
    assert resp.status_code == 200 and resp.json()["container"] == "app"
    assert seen["container"] == "app" and seen["requests"]["cpu"] == "100m"
    assert seen["limits"]["memory"] == "256Mi"


def test_data_update_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    saved = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def update_resource_data(self, kind, name, namespace, data):
            saved.update(kind=kind, data=data)

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    ok = client.put(
        "/api/resources/data?cluster_id=1&kind=configmaps&name=cfg&namespace=default",
        json={"data": {"A": "1", "B": "2"}},
    )
    assert ok.status_code == 200 and ok.json()["keys"] == 2
    assert saved["kind"] == "configmaps" and saved["data"] == {"A": "1", "B": "2"}
    # secrets are writable too (user opted in)
    assert client.put(
        "/api/resources/data?cluster_id=1&kind=secrets&name=s&namespace=default",
        json={"data": {"pwd": "x"}},
    ).status_code == 200
    # non-data kinds rejected
    assert client.put(
        "/api/resources/data?cluster_id=1&kind=pods&name=p&namespace=default",
        json={"data": {}},
    ).status_code == 404


def test_topology_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def traffic_graph(self, namespace):
            return {
                "nodes": [
                    {"id": "ingress/default/web", "kind": "ingress", "name": "web", "namespace": "default"},
                    {"id": "service/default/web", "kind": "service", "name": "web", "namespace": "default"},
                    {"id": "pod/default/web-1", "kind": "pod", "name": "web-1", "namespace": "default", "status": "Running"},
                ],
                "edges": [
                    {"from": "ingress/default/web", "to": "service/default/web"},
                    {"from": "service/default/web", "to": "pod/default/web-1"},
                ],
            }

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.get("/api/metrics/topology?cluster_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["nodes"]) == 3 and len(body["edges"]) == 2
    assert body["nodes"][0]["kind"] == "ingress"


def test_budget_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def namespace_budget(self, namespace):
            return {
                "rows": [{"namespace": "default", "pods": 2, "cpu_req": 200,
                          "cpu_lim": 0, "mem_req": 256, "mem_lim": 0,
                          "no_requests": 1, "no_limits": 2}],
                "risks": [{"namespace": "default", "pod": "p1", "container": "app",
                           "reason": "sem limits"}],
                "risk_total": 1,
            }

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient(*a, **k))
    resp = client.get("/api/metrics/budget?cluster_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_total"] == 1 and body["rows"][0]["no_limits"] == 2
