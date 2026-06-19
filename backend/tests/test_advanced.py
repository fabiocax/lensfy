"""Tests for the advanced Kubernetes management features:
RBAC & security, multi-cluster search/compare/inventory, CRDs, capacity/rightsizing.

Endpoint wiring is exercised with a patched Kubernetes client; the pure
extraction/aggregation logic is unit-tested against fake SDK objects.
"""

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.kubernetes import resources as R
from app.kubernetes.client import KubernetesClient
from app.models.cluster import Cluster
from app.services import multicluster as mc_service
from app.services import workloads as workloads_service


def _bare_client(**attrs):
    """A KubernetesClient with just the snapshot cache wired (for _cached)."""
    kc = object.__new__(KubernetesClient)
    kc._snap_lock = threading.Lock()
    kc._snap = {}
    for k, v in attrs.items():
        setattr(kc, k, v)
    return kc


# --------------------------- security scan (unit) ---------------------------

def test_security_scan_flags_risky_pod():
    risky = NS(
        metadata=NS(name="bad", namespace="prod"),
        spec=NS(
            host_network=True, host_pid=False, host_ipc=False,
            automount_service_account_token=True,
            security_context=None,
            volumes=[NS(name="root", host_path=NS(path="/"))],
            containers=[NS(
                name="app", image="nginx:latest",
                security_context=NS(
                    privileged=True, allow_privilege_escalation=True,
                    run_as_non_root=None, run_as_user=None,
                    read_only_root_filesystem=False,
                    capabilities=NS(add=["NET_ADMIN", "SYS_ADMIN"]),
                ),
                resources=NS(requests=None, limits=None),
            )],
        ),
    )
    kc = _bare_client(_core=NS(list_pod_for_all_namespaces=lambda: NS(items=[risky])))
    out = kc.security_scan(None)

    rules = {f["rule"] for f in out["findings"]}
    assert {"hostNetwork", "privileged", "hostPath", "capabilities",
            "runAsRoot", "noLimits", "mutableTag"} <= rules
    assert out["counts"]["critical"] >= 2  # hostNetwork + privileged
    assert out["scanned"] == 1
    assert 0 <= out["score"] < 100


def test_security_scan_clean_pod_scores_high():
    safe = NS(
        metadata=NS(name="ok", namespace="prod"),
        spec=NS(
            host_network=False, host_pid=False, host_ipc=False,
            automount_service_account_token=False,
            security_context=NS(run_as_non_root=True, run_as_user=1000),
            volumes=[],
            containers=[NS(
                name="app", image="nginx:1.27.3",
                security_context=NS(
                    privileged=False, allow_privilege_escalation=False,
                    run_as_non_root=True, run_as_user=1000,
                    read_only_root_filesystem=True, capabilities=None,
                ),
                resources=NS(requests=None, limits={"cpu": "100m", "memory": "128Mi"}),
            )],
        ),
    )
    kc = _bare_client(_core=NS(list_pod_for_all_namespaces=lambda: NS(items=[safe])))
    out = kc.security_scan(None)
    assert out["findings"] == []
    assert out["score"] == 100


# --------------------------- RBAC subjects (unit) ---------------------------

def test_rbac_subjects_aggregates_and_flags_admin():
    admin_rule = NS(verbs=["*"], resources=["*"])
    view_rule = NS(verbs=["get", "list"], resources=["pods"])
    rbac = NS(
        list_cluster_role=lambda: NS(items=[
            NS(metadata=NS(name="cluster-admin"), rules=[admin_rule]),
        ]),
        list_role_for_all_namespaces=lambda: NS(items=[
            NS(metadata=NS(namespace="dev", name="viewer"), rules=[view_rule]),
        ]),
        list_cluster_role_binding=lambda: NS(items=[
            NS(metadata=NS(name="b1"),
               role_ref=NS(kind="ClusterRole", name="cluster-admin"),
               subjects=[NS(kind="User", name="alice", namespace=None)]),
        ]),
        list_role_binding_for_all_namespaces=lambda: NS(items=[
            NS(metadata=NS(namespace="dev", name="b2"),
               role_ref=NS(kind="Role", name="viewer"),
               subjects=[NS(kind="ServiceAccount", name="ci", namespace="dev")]),
        ]),
    )
    kc = _bare_client(_rbac=rbac)
    out = kc.rbac_subjects()

    by_name = {s["name"]: s for s in out["subjects"]}
    assert by_name["alice"]["cluster_admin"] is True
    assert "*" in by_name["alice"]["verbs"]
    assert by_name["ci"]["cluster_admin"] is False
    assert "pods" in by_name["ci"]["resources"]
    assert out["cluster_admins"] == 1


# --------------------------- capacity (unit) --------------------------------

def test_capacity_computes_requests_and_headroom():
    node = NS(
        metadata=NS(name="n1"),
        status=NS(allocatable={"cpu": "4", "memory": "8388608Ki", "pods": "110"}),
        spec=NS(unschedulable=False),
    )
    pod = NS(
        metadata=NS(name="p1", namespace="default"),
        status=NS(phase="Running"),
        spec=NS(node_name="n1", containers=[
            NS(resources=NS(requests={"cpu": "1", "memory": "1024Mi"}, limits=None)),
        ]),
    )
    kc = _bare_client(_core=NS(
        list_node=lambda: NS(items=[node]),
        list_pod_for_all_namespaces=lambda: NS(items=[pod]),
    ))
    # avoid metrics-server: shadow the bound method
    kc.cluster_top = lambda kind, nodes=None: {"available": False, "rows": []}

    out = kc.capacity()
    row = out["nodes"][0]
    assert row["cpu_alloc"] == 4000 and row["cpu_req"] == 1000
    assert row["cpu_req_pct"] == 25
    assert row["mem_req"] == 1024 and row["pods"] == 1
    assert out["totals"]["metrics_available"] is False


def test_rightsizing_unavailable_without_metrics():
    kc = _bare_client()
    kc.cluster_top = lambda kind, namespace=None: {
        "available": False, "message": "Metrics Server não está instalado", "rows": []
    }
    out = kc.rightsizing(None)
    assert out["available"] is False and out["rows"] == []


def test_rightsizing_flags_overprovisioned():
    node_top = {
        "available": True,
        "rows": [{"namespace": "default", "name": "p1", "cpu": 50, "memory": 100}],
    }
    pod = NS(
        metadata=NS(name="p1", namespace="default"),
        spec=NS(containers=[
            NS(resources=NS(requests={"cpu": "1", "memory": "1024Mi"},
                            limits={"cpu": "2", "memory": "2048Mi"})),
        ]),
    )
    kc = _bare_client(_core=NS(
        list_pod_for_all_namespaces=lambda: NS(items=[pod]),
    ))
    kc.cluster_top = lambda kind, namespace=None: node_top
    out = kc.rightsizing(None)
    assert out["available"] is True
    row = out["rows"][0]
    assert "CPU superdimensionada" in row["verdict"]
    assert "memória superdimensionada" in row["verdict"]
    assert row["cpu_rec"] == 60  # round(50 * 1.2)


# --------------------------- networkpolicies registry -----------------------

def test_networkpolicy_row_extraction():
    np = NS(
        metadata=NS(name="deny-all", namespace="prod", creation_timestamp=None),
        spec=NS(pod_selector=NS(match_labels={"app": "api"}),
                policy_types=["Ingress", "Egress"]),
    )
    row = R.RESOURCES["networkpolicies"].row_fn(np)
    assert row["name"] == "deny-all"
    assert row["pod_selector"] == "app=api"
    assert row["types"] == "Ingress, Egress"
    assert "networkpolicies" in R.MANIFEST_KINDS


# --------------------------- security endpoints -----------------------------

def test_security_scan_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def security_scan(self, namespace):
            return {"findings": [{"severity": "critical", "rule": "privileged",
                                  "namespace": namespace, "pod": "x", "container": "app",
                                  "detail": "privilegiado"}],
                    "counts": {"critical": 1}, "total": 1, "score": 90, "scanned": 5}

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient())
    resp = client.get("/api/security/scan?cluster_id=1&namespace=prod")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and body["findings"][0]["rule"] == "privileged"


def test_rbac_subjects_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def rbac_subjects(self):
            return {"subjects": [{"kind": "User", "name": "alice", "cluster_admin": True,
                                  "verbs": ["*"], "resources": ["*"], "binding_count": 1,
                                  "bindings": [], "namespace": None}],
                    "total": 1, "cluster_admins": 1}

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient())
    resp = client.get("/api/security/rbac/subjects?cluster_id=1")
    assert resp.status_code == 200 and resp.json()["cluster_admins"] == 1


def test_can_i_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def rbac_can_i(self, **kwargs):
            seen.update(kwargs)
            return {"allowed": True, "denied": False, "reason": "RBAC", "subject": "ci"}

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient())
    resp = client.post(
        "/api/security/rbac/can-i?cluster_id=1",
        json={"verb": "create", "resource": "deployments", "namespace": "dev",
              "group": "apps", "serviceaccount": "ci"},
    )
    assert resp.status_code == 200 and resp.json()["allowed"] is True
    assert seen["verb"] == "create" and seen["serviceaccount"] == "ci"


# --------------------------- CRD endpoints ----------------------------------

def test_crd_endpoints(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def list_crds(self):
            return {"rows": [{"name": "applications.argoproj.io", "group": "argoproj.io",
                              "kind": "Application", "plural": "applications",
                              "scope": "Namespaced", "versions": ["v1alpha1"],
                              "version": "v1alpha1", "age": "30d"}], "total": 1}

        def list_custom_resource(self, group, version, plural, namespace):
            return {"group": group, "version": version, "plural": plural,
                    "rows": [{"name": "guestbook", "namespace": "argocd", "age": "1h"}],
                    "total": 1}

        def get_custom_resource(self, group, version, plural, name, namespace):
            return f"kind: Application\nmetadata:\n  name: {name}\n"

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient())
    lst = client.get("/api/crds?cluster_id=1")
    assert lst.status_code == 200 and lst.json()["rows"][0]["kind"] == "Application"

    inst = client.get("/api/crds/instances?cluster_id=1&group=argoproj.io"
                      "&version=v1alpha1&plural=applications&namespace=argocd")
    assert inst.status_code == 200 and inst.json()["rows"][0]["name"] == "guestbook"

    man = client.get("/api/crds/manifest?cluster_id=1&group=argoproj.io"
                     "&version=v1alpha1&plural=applications&name=guestbook&namespace=argocd")
    assert man.status_code == 200 and "name: guestbook" in man.json()["yaml"]


# --------------------------- capacity endpoints -----------------------------

def test_capacity_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def capacity(self):
            return {"nodes": [{"name": "n1", "cpu_alloc": 4000, "cpu_req": 1000}],
                    "totals": {"metrics_available": True}}

        def rightsizing(self, namespace):
            return {"available": True, "rows": [{"namespace": namespace or "all",
                    "pod": "p1", "verdict": ["CPU superdimensionada"]}], "total": 1}

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient())
    cap = client.get("/api/capacity?cluster_id=1")
    assert cap.status_code == 200 and cap.json()["nodes"][0]["name"] == "n1"
    rs = client.get("/api/capacity/rightsizing?cluster_id=1&namespace=dev")
    assert rs.status_code == 200 and rs.json()["rows"][0]["verdict"]


# --------------------------- multi-cluster endpoints ------------------------

def _seed_two_clusters(db_session):
    db_session.add_all([
        Cluster(name="prod", context="ctx-prod"),
        Cluster(name="stg", context="ctx-stg"),
    ])
    db_session.commit()


def test_global_search_endpoint(client, db_session, monkeypatch):
    _seed_two_clusters(db_session)

    class FakeClient:
        def __init__(self, context):
            self.context = context

        def list_pods(self, namespace):
            return [NS(name=f"api-{self.context}", namespace="default"),
                    NS(name="other", namespace="default")]

    monkeypatch.setattr(mc_service, "get_client",
                        lambda context, path, insecure: FakeClient(context))
    resp = client.get("/api/multicluster/search?q=api&kinds=pods")
    assert resp.status_code == 200
    body = resp.json()
    # one "api-*" pod per cluster
    assert body["total"] == 2
    names = sorted(r["name"] for r in body["results"])
    assert names == ["api-ctx-prod", "api-ctx-stg"]


def test_global_search_empty_query_400(client, db_session):
    _seed_two_clusters(db_session)
    assert client.get("/api/multicluster/search?q=").status_code == 400


def test_global_search_reports_unreachable(client, db_session, monkeypatch):
    _seed_two_clusters(db_session)
    from app.kubernetes.client import KubernetesError

    def fake_get_client(context, path, insecure):
        if context == "ctx-stg":
            raise KubernetesError("connection refused")

        class Ok:
            def list_pods(self, namespace):
                return [NS(name="apiserver", namespace="kube-system")]
        return Ok()

    monkeypatch.setattr(mc_service, "get_client", fake_get_client)
    resp = client.get("/api/multicluster/search?q=api&kinds=pods")
    body = resp.json()
    assert body["total"] == 1
    assert len(body["errors"]) == 1 and body["errors"][0]["cluster_name"] == "stg"


def test_compare_endpoint(client, db_session, monkeypatch):
    _seed_two_clusters(db_session)

    class FakeClient:
        def __init__(self, context):
            self.context = context

        def cluster_overview(self):
            return {"version": "v1.30.0", "counts": {"pods": 10},
                    "nodes": {"ready": 3, "total": 3}, "pods": {"total": 10},
                    "deployments": {"total": 4}, "usage": {}, "warnings_total": 0}

    monkeypatch.setattr(mc_service, "get_client",
                        lambda context, path, insecure: FakeClient(context))
    resp = client.get("/api/multicluster/compare")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(c["reachable"] and c["version"] == "v1.30.0" for c in body["clusters"])


def test_inventory_endpoint(client, db_session, monkeypatch):
    db_session.add(Cluster(name="prod", context="ctx-prod", version="v1.30.0"))
    db_session.commit()

    class FakeClient:
        def list_pods(self, namespace):
            return [NS(name="a", namespace="default"), NS(name="b", namespace="kube-system")]

        def list_deployments(self, namespace):
            return [NS(name="d1", namespace="default")]

        def list_resource(self, kind, namespace):
            if kind == "services":
                return {"rows": [{"name": "svc", "namespace": "default"}]}
            return {"rows": []}

    monkeypatch.setattr(mc_service, "get_client", lambda *a, **k: FakeClient())
    resp = client.get("/api/multicluster/inventory?cluster_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster_name"] == "prod"
    assert body["kinds"]["pods"] == 2 and body["kinds"]["services"] == 1
    assert body["pods_per_namespace"] == {"default": 1, "kube-system": 1}
    assert "events" not in body["kinds"]


def test_inventory_unknown_cluster_404(client, db_session):
    assert client.get("/api/multicluster/inventory?cluster_id=999").status_code == 404
