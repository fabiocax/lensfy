"""Tests for dynamic API discovery (built-in + CRD resource types)."""

import threading
from types import SimpleNamespace as NS

from app.kubernetes.client import KubernetesClient
from app.models.cluster import Cluster
from app.services import workloads as workloads_service


def _res(kind, name, gv, group, namespaced=True, verbs=("list", "get"),
         preferred=True, short=()):
    return NS(kind=kind, name=name, group_version=gv, group=group,
              namespaced=namespaced, verbs=list(verbs), preferred=preferred,
              short_names=list(short))


def _bare(**attrs):
    kc = object.__new__(KubernetesClient)
    kc._snap_lock = threading.Lock()
    kc._snap = {}
    for k, v in attrs.items():
        setattr(kc, k, v)
    return kc


def test_discover_resources_groups_and_filters():
    found = [
        _res("Pod", "pods", "v1", ""),
        _res("Pod", "pods/status", "v1", ""),               # subrecurso -> ignorado
        _res("ComponentStatus", "componentstatuses", "v1", "", verbs=("get",)),  # sem list
        _res("VirtualService", "virtualservices", "networking.istio.io/v1beta1",
             "networking.istio.io", short=("vs",)),
        _res("Gateway", "gateways", "networking.istio.io/v1", "networking.istio.io", preferred=True),
        _res("Gateway", "gateways", "networking.istio.io/v1alpha3",
             "networking.istio.io", preferred=False),       # versão duplicada -> dedupe
    ]
    kc = _bare(_dynamic=NS(resources=NS(search=lambda: found)))
    out = kc.discover_resources()

    by_group = {g["group"]: g for g in out["groups"]}
    istio = {r["kind"]: r for r in by_group["networking.istio.io"]["resources"]}
    assert set(istio) == {"VirtualService", "Gateway"}
    assert istio["Gateway"]["apiVersion"] == "networking.istio.io/v1"  # preferida
    assert istio["VirtualService"]["shortNames"] == ["vs"]
    # core: Pod listável presente; subrecurso e sem-list ausentes
    core = {r["kind"] for r in by_group[""]["resources"]}
    assert "Pod" in core and "ComponentStatus" not in core
    assert all("/" not in r["name"] for g in out["groups"] for r in g["resources"])


def test_list_resource_dynamic_rows():
    class FakeRes:
        namespaced = True

        def get(self, namespace=None):
            return NS(to_dict=lambda: {"items": [
                {"metadata": {"name": "vs-a", "namespace": "default", "creationTimestamp": None}},
                {"metadata": {"name": "vs-b", "namespace": "prod", "creationTimestamp": None}},
            ]})

    kc = _bare(_dynamic=NS(resources=NS(get=lambda api_version, kind: FakeRes())))
    out = kc.list_resource_dynamic("networking.istio.io/v1beta1", "VirtualService")
    assert out["namespaced"] is True and out["total"] == 2
    assert {r["name"] for r in out["rows"]} == {"vs-a", "vs-b"}


def test_discovery_endpoints(client, db_session, monkeypatch):
    db_session.add(Cluster(name="c", context="ctx"))
    db_session.commit()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def discover_resources(self):
            return {"groups": [{"group": "gateway.networking.k8s.io", "resources": [
                {"kind": "HTTPRoute", "name": "httproutes",
                 "apiVersion": "gateway.networking.k8s.io/v1", "group": "gateway.networking.k8s.io",
                 "namespaced": True, "shortNames": [], "preferred": True}]}], "total": 1}

        def list_resource_dynamic(self, api_version, kind, namespace):
            return {"apiVersion": api_version, "kind": kind, "namespaced": True,
                    "rows": [{"name": "route-1", "namespace": "default", "age": "2d"}], "total": 1}

        def get_manifest_dynamic(self, api_version, kind, name, namespace):
            return f"kind: {kind}\nmetadata:\n  name: {name}\n"

    monkeypatch.setattr(workloads_service, "get_client", lambda *a, **k: FakeClient())
    disc = client.get("/api/discovery?cluster_id=1")
    assert disc.status_code == 200
    assert disc.json()["groups"][0]["resources"][0]["kind"] == "HTTPRoute"

    inst = client.get("/api/discovery/instances?cluster_id=1"
                      "&apiVersion=gateway.networking.k8s.io/v1&kind=HTTPRoute")
    assert inst.status_code == 200 and inst.json()["rows"][0]["name"] == "route-1"

    man = client.get("/api/discovery/manifest?cluster_id=1"
                     "&apiVersion=gateway.networking.k8s.io/v1&kind=HTTPRoute&name=route-1&namespace=default")
    assert man.status_code == 200 and "name: route-1" in man.json()["yaml"]
