"""Thin wrapper over the official kubernetes-python SDK.

Everything cluster-facing goes through here so the rest of the app never imports
``kubernetes`` directly. A client is bound to a single kubeconfig context.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient

from app.core.logging import get_logger
from app.schemas.workloads import (
    ClusterMetrics,
    DeploymentSummary,
    PodSummary,
)

logger = get_logger(__name__)

# Interactive shell: try bash, fall back to sh, without muting stderr (the prompt).
_SHELL_CMD = [
    "/bin/sh",
    "-c",
    "export TERM=xterm-256color HISTFILE=/dev/null; "
    "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi",
]

# Node shell: a privileged pod (hostPID) whose exec enters the host's namespaces
# via nsenter on PID 1 — the same trick Lens / kvaps node-shell use.
_NODE_SHELL_NS = "kube-system"
_NODE_SHELL_IMAGE = "alpine:3.20"
_NODE_SHELL_CMD = [
    "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
    "/bin/sh", "-c",
    "export TERM=xterm-256color HISTFILE=/dev/null; "
    "command -v bash >/dev/null 2>&1 && exec bash || exec sh",
]


class KubernetesError(RuntimeError):
    """Raised when a cluster operation fails or the context is unreachable."""


# Default request timeouts (connect, read) in seconds. Without these, a cluster
# that goes unreachable mid-call blocks the worker thread indefinitely and the
# thread pool eventually exhausts, freezing the whole app. The kubernetes SDK
# has no global default, so we inject one at the REST layer (covers typed APIs,
# the DynamicClient and create_from_yaml alike).
_DEFAULT_TIMEOUT = (5, 60)   # normal calls: fail fast on connect, generous read
_STREAM_TIMEOUT = (5, None)  # streaming (logs follow / watch): never read-timeout


def _install_default_timeout(api_client) -> None:
    """Make every request bound by a connect timeout (and a read timeout for
    non-streaming calls) unless the caller passed its own ``_request_timeout``.

    Streaming calls (``_preload_content=False`` — pod-log follow, ``watch``) get a
    connect-only bound so a long-lived stream is never killed by a read timeout.
    """
    rc = api_client.rest_client
    original = rc.request

    def request(method, url, *args, _preload_content=True, _request_timeout=None, **kwargs):
        if _request_timeout is None:
            _request_timeout = _DEFAULT_TIMEOUT if _preload_content else _STREAM_TIMEOUT
        return original(
            method, url, *args,
            _preload_content=_preload_content,
            _request_timeout=_request_timeout,
            **kwargs,
        )

    rc.request = request


def _cpu_milli(value: str | None) -> int:
    """Parse a k8s CPU quantity to millicores."""
    if not value:
        return 0
    try:
        if value.endswith("n"):
            return round(int(value[:-1]) / 1_000_000)
        if value.endswith("u"):
            return round(int(value[:-1]) / 1_000)
        if value.endswith("m"):
            return int(value[:-1])
        return round(float(value) * 1000)
    except ValueError:
        return 0


def _mem_mib(value: str | None) -> int:
    """Parse a k8s memory quantity to MiB."""
    if not value:
        return 0
    units = {"Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0, "Ti": 1024.0 * 1024}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            try:
                return round(float(value[:-2]) * factor)
            except ValueError:
                return 0
    try:
        return round(int(value) / (1024 * 1024))  # bytes
    except ValueError:
        return 0


def _parse_ts(value):
    """Parse an ISO-8601 timestamp string (raw API objects carry strings, not
    datetimes) into a tz-aware datetime, or return it unchanged if already one."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _short_age(ts) -> str | None:
    """Compact uptime/age string from a tz-aware datetime (e.g. '3d2h', '5m')."""
    if not ts:
        return None
    try:
        secs = max(0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:  # noqa: BLE001 - naive/odd timestamp
        return None
    d, h, m = int(secs // 86400), int((secs % 86400) // 3600), int((secs % 3600) // 60)
    if d:
        return f"{d}d{h}h" if h else f"{d}d"
    if h:
        return f"{h}h{m}m" if m else f"{h}h"
    return f"{m}m" if m else f"{int(secs)}s"


def _api_errors_msg(exceptions) -> str:
    """Extract human messages from a list of kubernetes ApiException."""
    msgs = []
    for exc in exceptions:
        try:
            msgs.append(json.loads(exc.body)["message"])
        except Exception:  # noqa: BLE001
            msgs.append(getattr(exc, "reason", None) or str(exc))
    return "; ".join(msgs)


def _dyn_err_msg(exc) -> str:
    """Human message from a dynamic-client / API error (body → summary → str)."""
    body = getattr(exc, "body", None)
    if body:
        try:
            return json.loads(body)["message"]
        except Exception:  # noqa: BLE001
            pass
    return getattr(exc, "summary", None) or getattr(exc, "reason", None) or str(exc)


# Server-managed metadata that only adds noise to a manifest diff.
_DIFF_DROP_META = {
    "managedFields", "resourceVersion", "uid", "generation",
    "creationTimestamp", "selfLink",
}


def _clean_for_diff(obj: dict) -> dict:
    """Strip server-owned fields (status, bookkeeping metadata, the
    last-applied-configuration annotation) so a diff shows only intent."""
    out = {k: v for k, v in obj.items() if k != "status"}
    meta = out.get("metadata")
    if isinstance(meta, dict):
        meta = {k: v for k, v in meta.items() if k not in _DIFF_DROP_META}
        ann = meta.get("annotations")
        if isinstance(ann, dict):
            ann = {k: v for k, v in ann.items()
                   if k != "kubectl.kubernetes.io/last-applied-configuration"}
            if ann:
                meta["annotations"] = ann
            else:
                meta.pop("annotations", None)
        out["metadata"] = meta
    return out


def _scalarize(v):
    """Compact a value for diff display (lists/dicts → short JSON)."""
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str, sort_keys=True)
        return s if len(s) <= 200 else s[:200] + "…"
    return v


def _diff_dicts(old, new, path: str = "") -> list[dict]:
    """Flat list of changed leaf paths between two nested values: each entry is
    ``{path, old, new}``. Mismatched types/lists compare by value (shown whole)."""
    changes: list[dict] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = f"{path}.{key}" if path else key
            if key not in old:
                changes.append({"path": sub, "old": None, "new": _scalarize(new[key])})
            elif key not in new:
                changes.append({"path": sub, "old": _scalarize(old[key]), "new": None})
            else:
                changes.extend(_diff_dicts(old[key], new[key], sub))
    elif old != new:
        changes.append({"path": path, "old": _scalarize(old), "new": _scalarize(new)})
    return changes


@dataclass(frozen=True)
class ContextInfo:
    name: str
    cluster: str | None
    server: str | None


def list_contexts(kubeconfig_path: str | None = None) -> list[ContextInfo]:
    """Return the contexts declared in a kubeconfig file (``~`` expanded)."""
    import os

    if kubeconfig_path:
        kubeconfig_path = os.path.expanduser(kubeconfig_path)
    try:
        contexts, _active = config.list_kube_config_contexts(config_file=kubeconfig_path)
    except config.ConfigException as exc:  # pragma: no cover - depends on env
        raise KubernetesError(f"Não foi possível ler o kubeconfig: {exc}") from exc

    infos: list[ContextInfo] = []
    for ctx in contexts or []:
        ctx_ctx = ctx.get("context", {})
        infos.append(
            ContextInfo(name=ctx["name"], cluster=ctx_ctx.get("cluster"), server=None)
        )
    return infos


def contexts_from_kubeconfig(
    path: str | None = None, content: str | None = None
) -> list[ContextInfo]:
    """List contexts from a kubeconfig given by path or by raw content."""
    if content:
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(content)
            tmp = fh.name
        try:
            return list_contexts(tmp)
        finally:
            os.unlink(tmp)
    return list_contexts(path)


class KubernetesClient:
    """API clients scoped to one kubeconfig context."""

    def __init__(
        self,
        context: str,
        kubeconfig_path: str | None = None,
        insecure: bool = False,
    ) -> None:
        self.context = context
        try:
            api_client = config.new_client_from_config(
                config_file=kubeconfig_path, context=context
            )
        except config.ConfigException as exc:
            raise KubernetesError(
                f"Could not load context {context!r}: {exc}"
            ) from exc

        if insecure:
            # Rebuild the client with TLS verification off — the REST pool bakes
            # verify_ssl at construction, so flipping it afterwards has no effect.
            import urllib3

            conf = api_client.configuration
            conf.verify_ssl = False
            conf.ssl_ca_cert = None
            urllib3.disable_warnings()
            api_client = client.ApiClient(conf)

        _install_default_timeout(api_client)
        self._api_client = api_client
        self._dynamic: DynamicClient | None = None
        self._core = client.CoreV1Api(api_client)
        self._apps = client.AppsV1Api(api_client)
        self._net = client.NetworkingV1Api(api_client)
        self._batch = client.BatchV1Api(api_client)
        self._storage = client.StorageV1Api(api_client)
        self._rbac = client.RbacAuthorizationV1Api(api_client)
        self._version = client.VersionApi(api_client)
        # Short-lived snapshot cache for the hot polled paths (metrics/overview).
        # Because clients are now shared (get_client), this is shared across all
        # dashboard tabs/sockets, collapsing N pollers into one set of API calls.
        self._snap_lock = threading.Lock()
        self._snap: dict[str, tuple[float, object]] = {}

    # --- internals --------------------------------------------------------

    def _cached(self, key: str, ttl: float, build):
        """Memoise ``build()`` for ``ttl`` seconds (per shared client instance)."""
        now = time.monotonic()
        with self._snap_lock:
            hit = self._snap.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
        value = build()
        with self._snap_lock:
            self._snap[key] = (now, value)
        return value

    def _guard(self, action: str, fn):
        """Run an SDK call, normalising API/connection errors to KubernetesError."""
        try:
            return fn()
        except ApiException as exc:
            raise KubernetesError(f"{action} failed: {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001 - connection errors, etc.
            raise KubernetesError(f"{action} failed: {exc}") from exc

    def _resolve_container(
        self, name: str, namespace: str, container: str | None
    ) -> str | None:
        """Default to the pod's first container when none is given.

        Multi-container pods (e.g. with an istio sidecar) reject logs/exec
        without an explicit container, so we pick the first one.
        """
        if container:
            return container
        pod = self._core.read_namespaced_pod(name=name, namespace=namespace)
        containers = pod.spec.containers or []
        return containers[0].name if containers else None

    # --- cluster info -----------------------------------------------------

    def server_version(self) -> str | None:
        # Best-effort probe with a bounded timeout so importing an unreachable
        # cluster fails fast instead of hanging the request.
        try:
            return self._version.get_code(_request_timeout=8).git_version
        except Exception as exc:  # noqa: BLE001
            logger.warning("version check failed for %s: %s", self.context, exc)
            return None

    def ping(self) -> bool:
        return self.server_version() is not None

    # --- workloads --------------------------------------------------------

    def list_pods(self, namespace: str | None = None) -> list[PodSummary]:
        items = self._guard(
            "list pods",
            lambda: (
                self._core.list_namespaced_pod(namespace).items
                if namespace
                else self._core.list_pod_for_all_namespaces().items
            ),
        )
        return [self._pod_summary(p) for p in items]

    def list_deployments(self, namespace: str | None = None) -> list[DeploymentSummary]:
        items = self._guard(
            "list deployments",
            lambda: (
                self._apps.list_namespaced_deployment(namespace).items
                if namespace
                else self._apps.list_deployment_for_all_namespaces().items
            ),
        )
        return [self._deployment_summary(d) for d in items]

    def scale_deployment(
        self, name: str, namespace: str, replicas: int
    ) -> DeploymentSummary:
        def _scale():
            self._apps.patch_namespaced_deployment_scale(
                name=name, namespace=namespace, body={"spec": {"replicas": replicas}}
            )
            return self._apps.read_namespaced_deployment(name=name, namespace=namespace)

        return self._deployment_summary(self._guard("scale deployment", _scale))

    def delete_pod(self, name: str, namespace: str) -> None:
        self._guard(
            "delete pod",
            lambda: self._core.delete_namespaced_pod(name=name, namespace=namespace),
        )

    # --- metrics / dashboard ---------------------------------------------

    def cluster_metrics(self) -> ClusterMetrics:
        # Polled by /ws/metrics per socket; cache briefly so many open dashboards
        # don't each fan out 6 full-cluster list calls every few seconds.
        def _build():
            return self._guard(
                "fetch metrics",
                lambda: ClusterMetrics(
                    nodes=len(self._core.list_node().items),
                    namespaces=len(self._core.list_namespace().items),
                    pods=len(self._core.list_pod_for_all_namespaces().items),
                    deployments=len(self._apps.list_deployment_for_all_namespaces().items),
                    services=len(self._core.list_service_for_all_namespaces().items),
                    ingresses=len(self._net.list_ingress_for_all_namespaces().items),
                ),
            )

        return self._cached("metrics", 8.0, _build)

    # --- live metrics (metrics-server: metrics.k8s.io) -------------------

    def cluster_top(
        self, kind: str, namespace: str | None = None, nodes=None
    ) -> dict:
        """Node/pod CPU+memory usage from metrics-server.

        Returns ``{available, rows}``; ``available`` is False with a message when
        metrics-server isn't installed (common) so the UI can explain it.
        ``nodes`` lets a caller (e.g. cluster_overview) pass an already-fetched
        node list so we don't re-list nodes just for capacity.
        """
        from kubernetes import client as _client

        api = _client.CustomObjectsApi(self._api_client)
        try:
            if kind == "nodes":
                data = api.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")
                node_items = nodes if nodes is not None else self._core.list_node().items
                caps = {
                    n.metadata.name: (n.status.capacity or {})
                    for n in node_items
                }
                rows = []
                for it in data.get("items", []):
                    name = it["metadata"]["name"]
                    usage = it.get("usage", {})
                    cap = caps.get(name, {})
                    cpu = _cpu_milli(usage.get("cpu"))
                    cpu_cap = _cpu_milli(cap.get("cpu"))
                    mem = _mem_mib(usage.get("memory"))
                    mem_cap = _mem_mib(cap.get("memory"))
                    rows.append({
                        "name": name, "namespace": None,
                        "cpu": cpu, "cpu_cap": cpu_cap,
                        "cpu_pct": round(cpu / cpu_cap * 100) if cpu_cap else None,
                        "memory": mem, "memory_cap": mem_cap,
                        "memory_pct": round(mem / mem_cap * 100) if mem_cap else None,
                    })
            else:  # pods
                if namespace:
                    data = api.list_namespaced_custom_object(
                        "metrics.k8s.io", "v1beta1", namespace, "pods"
                    )
                else:
                    data = api.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
                rows = []
                for it in data.get("items", []):
                    cpu = sum(_cpu_milli(c["usage"].get("cpu")) for c in it.get("containers", []))
                    mem = sum(_mem_mib(c["usage"].get("memory")) for c in it.get("containers", []))
                    rows.append({
                        "name": it["metadata"]["name"],
                        "namespace": it["metadata"].get("namespace"),
                        "cpu": cpu, "cpu_cap": None, "cpu_pct": None,
                        "memory": mem, "memory_cap": None, "memory_pct": None,
                    })
            rows.sort(key=lambda r: r["cpu"], reverse=True)
            return {"available": True, "message": None, "rows": rows}
        except ApiException as exc:
            if exc.status == 404:
                return {
                    "available": False,
                    "message": "Metrics Server não está instalado neste cluster.",
                    "rows": [],
                }
            raise KubernetesError(f"top {kind} failed: {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise KubernetesError(f"top {kind} failed: {exc}") from exc

    def cluster_overview(self) -> dict:
        """Rich dashboard snapshot: counts + health (nodes/pods/deployments),
        recent warnings, cluster version/usage. A handful of list calls.

        Cached briefly (the dashboard polls this ~every 10s and several tabs may
        poll at once) so we don't re-run ~8 list calls per poll per client.
        """

        def _do():
            nodes = self._core.list_node().items
            pods = self._core.list_pod_for_all_namespaces().items
            deps = self._apps.list_deployment_for_all_namespaces().items

            # Nodes: ready/total, kubelet versions, total capacity.
            def _ready(obj):
                return any(
                    c.type == "Ready" and c.status == "True"
                    for c in (obj.status.conditions or [])
                )

            nodes_ready = sum(1 for n in nodes if _ready(n))
            versions = sorted(
                {
                    n.status.node_info.kubelet_version
                    for n in nodes
                    if n.status and n.status.node_info
                }
            )
            cpu_cap = sum(_cpu_milli((n.status.capacity or {}).get("cpu")) for n in nodes)
            mem_cap = sum(_mem_mib((n.status.capacity or {}).get("memory")) for n in nodes)

            # Pods: phase breakdown, restarts, not-ready running pods.
            phases: dict[str, int] = {}
            restarts = 0
            not_ready = 0
            for p in pods:
                phase = (p.status.phase if p.status else None) or "Unknown"
                phases[phase] = phases.get(phase, 0) + 1
                cs = (p.status.container_statuses if p.status else None) or []
                restarts += sum(c.restart_count or 0 for c in cs)
                if phase == "Running" and (not cs or not all(c.ready for c in cs)):
                    not_ready += 1

            # Deployments: how many aren't fully available.
            dep_unhealthy = sum(
                1
                for d in deps
                if (d.status.ready_replicas or 0) < (d.spec.replicas or 0)
            )

            # Recent warning events (bounded, sorted newest-first client-side).
            warnings: list[dict] = []
            try:
                evs = self._core.list_event_for_all_namespaces(
                    field_selector="type=Warning", limit=400
                ).items

                def _ts(e):
                    return e.last_timestamp or e.event_time or e.metadata.creation_timestamp

                evs = [e for e in evs if _ts(e)]
                evs.sort(key=_ts, reverse=True)
                for e in evs[:8]:
                    obj = e.involved_object
                    warnings.append({
                        "reason": e.reason,
                        "message": (e.message or "").strip(),
                        "object": f"{obj.kind}/{obj.name}" if obj else "",
                        "namespace": e.metadata.namespace,
                        "count": e.count or 1,
                        "time": _ts(e).isoformat() if _ts(e) else None,
                    })
                warnings_total = len(evs)
            except ApiException:
                warnings_total = 0

            # Cluster-wide usage from metrics-server, if present.
            usage = {"available": False}
            try:
                top = self.cluster_top("nodes", nodes=nodes)  # reuse fetched nodes
                if top.get("available") and top["rows"]:
                    cpu_used = sum(r["cpu"] for r in top["rows"])
                    mem_used = sum(r["memory"] for r in top["rows"])
                    usage = {
                        "available": True,
                        "cpu_used": cpu_used, "cpu_cap": cpu_cap,
                        "cpu_pct": round(cpu_used / cpu_cap * 100) if cpu_cap else None,
                        "mem_used": mem_used, "mem_cap": mem_cap,
                        "mem_pct": round(mem_used / mem_cap * 100) if mem_cap else None,
                    }
            except KubernetesError:
                pass

            return {
                "counts": {
                    "nodes": len(nodes),
                    "namespaces": len(self._core.list_namespace().items),
                    "pods": len(pods),
                    "deployments": len(deps),
                    "services": len(self._core.list_service_for_all_namespaces().items),
                    "ingresses": len(self._net.list_ingress_for_all_namespaces().items),
                },
                "nodes": {"ready": nodes_ready, "total": len(nodes), "versions": versions},
                "pods": {
                    "total": len(pods), "phases": phases,
                    "restarts": restarts, "not_ready": not_ready,
                },
                "deployments": {"total": len(deps), "unhealthy": dep_unhealthy},
                "warnings": warnings,
                "warnings_total": warnings_total,
                "usage": usage,
                "version": self.server_version(),
            }

        return self._cached("overview", 8.0, lambda: self._guard("cluster overview", _do))

    # --- generic resources (Explorer tree) -------------------------------

    def list_resource(self, kind: str, namespace: str | None = None) -> dict:
        """List any registered resource kind as a column/row table.

        Backs the read-only Explorer views (nodes, services, jobs, …). The
        per-kind column set and field extraction live in ``resources.py``.
        """
        from app.kubernetes.resources import RESOURCES

        rdef = RESOURCES.get(kind)
        if rdef is None:
            raise KubernetesError(f"Unknown resource kind: {kind}")

        items = self._guard(f"list {kind}", lambda: rdef.list_fn(self, namespace))
        return {
            "kind": kind,
            "namespaced": rdef.namespaced,
            "columns": [{"key": k, "label": label} for k, label in rdef.columns],
            "rows": [rdef.row_fn(obj) for obj in items],
        }

    def get_resource_data(self, kind: str, name: str, namespace: str) -> dict:
        """Return the key/value data of a Secret (base64-decoded) or ConfigMap."""
        import base64

        if kind == "secrets":
            sec = self._guard(
                "read secret", lambda: self._core.read_namespaced_secret(name, namespace)
            )
            items = []
            for key, val in (sec.data or {}).items():
                try:
                    decoded = base64.b64decode(val).decode("utf-8")
                except Exception:  # noqa: BLE001 - binary secret
                    decoded = "(conteúdo binário)"
                items.append({"key": key, "value": decoded})
            return {"kind": kind, "name": name, "namespace": namespace,
                    "type": sec.type, "items": items}
        if kind == "configmaps":
            cm = self._guard(
                "read configmap",
                lambda: self._core.read_namespaced_config_map(name, namespace),
            )
            items = [{"key": k, "value": v} for k, v in (cm.data or {}).items()]
            return {"kind": kind, "name": name, "namespace": namespace,
                    "type": None, "items": items}
        raise KubernetesError(f"{kind} não suporta visualização de dados")

    def update_resource_data(self, kind: str, name: str, namespace: str, data: dict):
        """Replace a ConfigMap's ``data`` / Secret's contents with ``data`` (a flat
        key->string map). Read-modify-write so removed keys actually disappear
        (a merge patch can't delete map keys). Secrets use ``string_data`` and let
        the API base64-encode; we clear the old ``data`` so stale keys don't linger.
        """
        clean = {str(k): ("" if v is None else str(v)) for k, v in (data or {}).items()}

        def _do():
            if kind == "configmaps":
                cm = self._core.read_namespaced_config_map(name, namespace)
                cm.data = clean
                cm.binary_data = None
                self._core.replace_namespaced_config_map(name, namespace, cm)
            elif kind == "secrets":
                sec = self._core.read_namespaced_secret(name, namespace)
                sec.data = {}          # drop existing base64 keys
                sec.string_data = clean  # API encodes these to data
                self._core.replace_namespaced_secret(name, namespace, sec)
            else:
                raise KubernetesError(f"{kind} não suporta edição de dados")

        self._guard(f"update {kind} data", _do)

    # --- YAML manifest (view/edit/apply) ---------------------------------

    def _dyn(self) -> DynamicClient:
        if self._dynamic is None:
            self._dynamic = DynamicClient(self._api_client)
        return self._dynamic

    @staticmethod
    def _to_yaml(obj: dict) -> str:
        # managedFields is huge server-side bookkeeping; drop it like kubectl edit.
        obj.get("metadata", {}).pop("managedFields", None)
        return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, width=4096)

    def get_manifest(self, kind: str, name: str, namespace: str | None = None) -> str:
        """Fetch a resource as YAML via the dynamic client."""
        from app.kubernetes.resources import MANIFEST_KINDS

        api_version, k8s_kind, namespaced = MANIFEST_KINDS[kind]

        def _do():
            res = self._dyn().resources.get(api_version=api_version, kind=k8s_kind)
            obj = res.get(name=name, namespace=namespace if namespaced else None)
            return self._to_yaml(obj.to_dict())

        return self._guard(f"get {kind} manifest", _do)

    def get_object(self, kind: str, name: str, namespace: str | None = None) -> dict:
        """Fetch a resource as a plain dict (for the detail panel)."""
        from app.kubernetes.resources import MANIFEST_KINDS

        api_version, k8s_kind, namespaced = MANIFEST_KINDS[kind]

        def _do():
            res = self._dyn().resources.get(api_version=api_version, kind=k8s_kind)
            obj = res.get(name=name, namespace=namespace if namespaced else None)
            data = obj.to_dict()
            data.get("metadata", {}).pop("managedFields", None)
            return data

        return self._guard(f"get {kind}", _do)

    def apply_manifest(
        self, kind: str, name: str, namespace: str | None, yaml_text: str
    ) -> str:
        """Replace a resource from edited YAML (PUT), returning the updated YAML."""
        from app.kubernetes.resources import MANIFEST_KINDS

        api_version, k8s_kind, namespaced = MANIFEST_KINDS[kind]
        try:
            body = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise KubernetesError(f"YAML inválido: {exc}") from exc
        if not isinstance(body, dict):
            raise KubernetesError("YAML inválido: esperado um objeto de recurso")

        ns = namespace if namespaced else None

        def _do():
            res = self._dyn().resources.get(api_version=api_version, kind=k8s_kind)
            meta = body.setdefault("metadata", {})
            # Um replace (PUT) exige o resourceVersion atual. O YAML editado carrega
            # o da abertura, que fica obsoleto assim que algo muda o objeto (status,
            # controllers, watch) — daí o 409 intermitente. Realinhamos ao estado
            # vivo imediatamente antes do replace e repetimos se ainda houver corrida.
            last_exc: ApiException | None = None
            for _ in range(4):
                current = res.get(name=name, namespace=ns).to_dict()
                meta["resourceVersion"] = current.get("metadata", {}).get("resourceVersion")
                try:
                    updated = res.replace(body=body, name=name, namespace=ns)
                    return self._to_yaml(updated.to_dict())
                except ApiException as exc:
                    if exc.status == 409:  # conflito de versão: refaz com a versão nova
                        last_exc = exc
                        continue
                    raise
            raise KubernetesError(
                f"apply {kind} manifest failed: conflito de versão persistente "
                f"(o recurso muda mais rápido que o save) — {getattr(last_exc, 'reason', '')}"
            )

        return self._guard(f"apply {kind} manifest", _do)

    def deploy_manifests(
        self, yaml_text: str, default_namespace: str = "default"
    ) -> list[dict]:
        """Create every resource in a (possibly multi-document) YAML blob.

        Uses ``kubernetes.utils.create_from_yaml`` per document so we can report
        a per-resource status (created / error) instead of failing the batch.
        """
        from kubernetes import utils

        try:
            docs = [d for d in yaml.safe_load_all(yaml_text) if isinstance(d, dict)]
        except yaml.YAMLError as exc:
            raise KubernetesError(f"YAML inválido: {exc}") from exc
        if not docs:
            raise KubernetesError("Nenhum documento YAML válido encontrado")

        results: list[dict] = []
        for doc in docs:
            meta = doc.get("metadata") or {}
            entry = {
                "kind": doc.get("kind", "?"),
                "name": meta.get("name", "?"),
                "namespace": meta.get("namespace") or default_namespace,
            }
            try:
                utils.create_from_yaml(
                    self._api_client, yaml_objects=[doc], namespace=entry["namespace"]
                )
                results.append({**entry, "status": "created"})
            except utils.FailToCreateError as exc:
                results.append(
                    {**entry, "status": "error", "message": _api_errors_msg(exc.api_exceptions)}
                )
            except Exception as exc:  # noqa: BLE001
                results.append({**entry, "status": "error", "message": str(exc)})
        return results

    # --- logs -------------------------------------------------------------

    def pod_logs(
        self, name: str, namespace: str, container: str | None = None,
        tail_lines: int = 200,
    ) -> str:
        """Non-streaming snapshot of a pod's recent logs (for the AI agent)."""
        container = self._resolve_container(name, namespace, container)
        return self._guard(
            "read logs",
            lambda: self._core.read_namespaced_pod_log(
                name=name, namespace=namespace, container=container,
                tail_lines=tail_lines,
            ),
        )

    def stream_logs(
        self,
        name: str,
        namespace: str,
        container: str | None = None,
        tail_lines: int = 200,
    ):
        """Return a ``(response, line_generator)`` pair tailing a pod's logs.

        ``tail_lines`` seeds the stream with recent history before following.
        We iterate the raw urllib3 response (not ``watch.Watch``, which is for
        list endpoints and rejects ``read_namespaced_pod_log``). The caller owns
        the response and must ``.close()`` it to end the stream. Backs /ws/logs.
        """
        container = self._resolve_container(name, namespace, container)
        resp = self._core.read_namespaced_pod_log(
            name=name,
            namespace=namespace,
            container=container,
            follow=True,
            tail_lines=tail_lines,
            _preload_content=False,
        )

        def _lines():
            buf = b""
            try:
                for chunk in resp.stream(amt=1024, decode_content=False):
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        yield line.decode("utf-8", "replace")
                if buf:
                    yield buf.decode("utf-8", "replace")
            finally:
                resp.release_conn()

        return resp, _lines()

    # kind -> (api attribute, method suffix). Drives the typed watch list method.
    _WATCH_METHODS = {
        "pods": ("_core", "pod"), "deployments": ("_apps", "deployment"),
        "statefulsets": ("_apps", "stateful_set"), "daemonsets": ("_apps", "daemon_set"),
        "jobs": ("_batch", "job"), "cronjobs": ("_batch", "cron_job"),
        "services": ("_core", "service"), "configmaps": ("_core", "config_map"),
        "secrets": ("_core", "secret"), "pvc": ("_core", "persistent_volume_claim"),
        "ingress": ("_net", "ingress"), "events": ("_core", "event"),
        "networkpolicies": ("_net", "network_policy"),
        "limitranges": ("_core", "limit_range"),
        "resourcequotas": ("_core", "resource_quota"),
        "nodes": ("_core", "node"), "namespaces": ("_core", "namespace"),
        "storageclasses": ("_storage", "storage_class"),
        "roles": ("_rbac", "role"), "clusterroles": ("_rbac", "cluster_role"),
        "rolebindings": ("_rbac", "role_binding"),
        "clusterrolebindings": ("_rbac", "cluster_role_binding"),
    }

    def watch_resource(self, kind: str, namespace: str | None = None):
        """Return ``(Watch, generator, formatter)`` streaming live changes for a
        kind. Each yielded item is ``{"type": ADDED|MODIFIED|DELETED, "object":
        <typed obj>}``; ``formatter(obj)`` produces the same row dict the list
        endpoints return. Caller owns the Watch (``.stop()``). Backs /ws/watch.
        """
        from kubernetes import watch

        from app.kubernetes.resources import RESOURCES

        spec = self._WATCH_METHODS.get(kind)
        if spec is None:
            raise KubernetesError(f"watch não suportado para {kind}")
        api_attr, suffix = spec
        api = getattr(self, api_attr)

        if kind == "pods":
            namespaced, fmt = True, lambda o: self._pod_summary(o).model_dump()
        elif kind == "deployments":
            namespaced, fmt = True, lambda o: self._deployment_summary(o).model_dump()
        else:
            rdef = RESOURCES[kind]
            namespaced, fmt = rdef.namespaced, rdef.row_fn

        if namespaced and namespace:
            method, kwargs = getattr(api, f"list_namespaced_{suffix}"), {"namespace": namespace}
        elif namespaced:
            method, kwargs = getattr(api, f"list_{suffix}_for_all_namespaces"), {}
        else:
            method, kwargs = getattr(api, f"list_{suffix}"), {}

        w = watch.Watch()
        return w, w.stream(method, **kwargs), fmt

    def stream_events(self, namespace: str | None = None):
        """Return a ``(Watch, generator)`` streaming cluster events.

        Unlike pod logs, events are a list endpoint, so ``watch.Watch`` works.
        Each yielded item is a dict ``{"type": ADDED|MODIFIED|DELETED,
        "object": V1Event}``. The caller owns the Watch and must ``.stop()`` it.
        Backs /ws/events.
        """
        from kubernetes import watch

        w = watch.Watch()
        if namespace:
            stream = w.stream(self._core.list_namespaced_event, namespace)
        else:
            stream = w.stream(self._core.list_event_for_all_namespaces)
        return w, stream

    # --- exec / terminal --------------------------------------------------

    def exec_shell(
        self,
        name: str,
        namespace: str,
        container: str | None = None,
        command: list[str] | None = None,
    ):
        """Open an interactive shell into a pod, returning a kubernetes WSClient.

        Default command tries bash then sh inside the container; pass ``command``
        to run something else (e.g. the node-shell nsenter). Don't redirect the
        session's stderr — the prompt (PS1) is written there. The returned client
        is a synchronous websocket (``is_open``/``update``/``read_stdout``/
        ``write_stdin``/``write_channel``); the caller bridges it and ``.close()``s
        it. Backs /ws/terminal.
        """
        from kubernetes.stream import stream

        if command is None:
            command = _SHELL_CMD
        container = self._resolve_container(name, namespace, container)
        # Use a DEDICATED ApiClient for the exec. ``stream()`` temporarily swaps
        # ``api_client.request`` with a websocket call; since clients are now shared
        # (get_client cache), two overlapping exec sessions on the same cluster would
        # race on that swap and could leave the shared client stuck routing REST
        # calls through the websocket path. A private client per exec is isolated
        # (and cheap — exec sessions are long-lived and few).
        exec_api_client = client.ApiClient(self._api_client.configuration)
        exec_core = client.CoreV1Api(exec_api_client)
        ws = stream(
            exec_core.connect_get_namespaced_pod_exec,
            name,
            namespace,
            command=command,
            container=container,
            stderr=True,
            stdin=True,
            stdout=True,
            tty=True,
            _preload_content=False,
        )
        # Attach the owning ApiClient so the caller can release its connection
        # pool when the session ends — otherwise the dedicated client (and its
        # urllib3 PoolManager) is leaked for every terminal/node-shell session.
        try:
            ws._lensfy_api_client = exec_api_client
        except Exception:  # noqa: BLE001 - WSClient should accept attributes
            pass
        return ws

    # --- workload operations (scale / restart / delete) ------------------

    def scale_workload(self, kind: str, name: str, namespace: str, replicas: int):
        body = {"spec": {"replicas": replicas}}

        def _do():
            if kind == "deployments":
                self._apps.patch_namespaced_deployment_scale(name, namespace, body)
            elif kind == "statefulsets":
                self._apps.patch_namespaced_stateful_set_scale(name, namespace, body)
            else:
                raise KubernetesError(f"{kind} não pode ser escalado")

        self._guard(f"scale {kind}", _do)

    def restart_workload(self, kind: str, name: str, namespace: str):
        # Rollout restart = bump a pod-template annotation, like `kubectl rollout restart`.
        stamp = datetime.now(timezone.utc).isoformat()
        patch = {
            "spec": {
                "template": {
                    "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}
                }
            }
        }

        def _do():
            if kind == "deployments":
                self._apps.patch_namespaced_deployment(name, namespace, patch)
            elif kind == "statefulsets":
                self._apps.patch_namespaced_stateful_set(name, namespace, patch)
            elif kind == "daemonsets":
                self._apps.patch_namespaced_daemon_set(name, namespace, patch)
            else:
                raise KubernetesError(f"{kind} não suporta restart")

        self._guard(f"restart {kind}", _do)

    def trigger_cronjob(self, name: str, namespace: str) -> str:
        """Create a Job now from a CronJob's jobTemplate (`kubectl create job --from`)."""
        from kubernetes import client as kc

        def _do():
            cj = self._batch.read_namespaced_cron_job(name, namespace)
            stamp = int(datetime.now(timezone.utc).timestamp())
            job_name = f"{name[:40]}-manual-{stamp}"
            job = kc.V1Job(
                api_version="batch/v1",
                kind="Job",
                metadata=kc.V1ObjectMeta(
                    name=job_name,
                    namespace=namespace,
                    annotations={"cronjob.kubernetes.io/instantiate": "manual"},
                    owner_references=[
                        kc.V1OwnerReference(
                            api_version="batch/v1",
                            kind="CronJob",
                            name=cj.metadata.name,
                            uid=cj.metadata.uid,
                            controller=True,
                            block_owner_deletion=True,
                        )
                    ],
                ),
                spec=cj.spec.job_template.spec,
            )
            self._batch.create_namespaced_job(namespace, job)
            return job_name

        return self._guard("trigger cronjob", _do)

    def set_cronjob_suspend(self, name: str, namespace: str, suspend: bool):
        """Suspend (disable) or resume a CronJob via `.spec.suspend`."""
        self._guard(
            "suspend cronjob",
            lambda: self._batch.patch_namespaced_cron_job(
                name, namespace, {"spec": {"suspend": suspend}}
            ),
        )

    def set_container_resources(
        self, kind: str, name: str, namespace: str,
        container: str, requests: dict, limits: dict,
    ):
        """Set a container's requests/limits on a workload (the pod template).

        Strategic-merge patch: the ``containers`` list merges by ``name``, so we
        only send the targeted container. Empty maps clear that side.
        """
        res = {}
        if requests:
            res["requests"] = requests
        if limits:
            res["limits"] = limits
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": container, "resources": res}
        ]}}}}

        def _do():
            if kind == "deployments":
                self._apps.patch_namespaced_deployment(name, namespace, patch)
            elif kind == "statefulsets":
                self._apps.patch_namespaced_stateful_set(name, namespace, patch)
            elif kind == "daemonsets":
                self._apps.patch_namespaced_daemon_set(name, namespace, patch)
            else:
                raise KubernetesError(f"{kind} não suporta edição de recursos")

        self._guard(f"set resources on {kind}", _do)

    def namespace_budget(self, namespace: str | None = None) -> dict:
        """Per-namespace requests/limits budget + SLA risk (cached ~8s).

        Returns ``{rows:[{namespace, pods, cpu_req, cpu_lim, mem_req, mem_lim,
        no_requests, no_limits, quota}], risks:[{namespace,pod,reason}]}`` summing
        container requests/limits (cpu millicores, mem MiB), flagging pods/containers
        without requests or limits (OOM/SLA risk), and attaching any ResourceQuota.
        """

        def _do():
            pods = (
                self._core.list_namespaced_pod(namespace).items if namespace
                else self._core.list_pod_for_all_namespaces().items
            )
            agg: dict[str, dict] = {}
            risks: list[dict] = []
            for p in pods:
                ns = p.metadata.namespace
                a = agg.setdefault(ns, {
                    "namespace": ns, "pods": 0,
                    "cpu_req": 0, "cpu_lim": 0, "mem_req": 0, "mem_lim": 0,
                    "no_requests": 0, "no_limits": 0,
                })
                a["pods"] += 1
                for c in (p.spec.containers or []):
                    r = (c.resources.requests or {}) if c.resources else {}
                    l = (c.resources.limits or {}) if c.resources else {}
                    a["cpu_req"] += _cpu_milli(r.get("cpu"))
                    a["cpu_lim"] += _cpu_milli(l.get("cpu"))
                    a["mem_req"] += _mem_mib(r.get("memory"))
                    a["mem_lim"] += _mem_mib(l.get("memory"))
                    miss_r = not r.get("cpu") and not r.get("memory")
                    miss_l = not l.get("cpu") and not l.get("memory")
                    if miss_r:
                        a["no_requests"] += 1
                    if miss_l:
                        a["no_limits"] += 1
                    if miss_r or miss_l:
                        why = []
                        if miss_r:
                            why.append("sem requests")
                        if miss_l:
                            why.append("sem limits")
                        risks.append({
                            "namespace": ns, "pod": p.metadata.name,
                            "container": c.name, "reason": " · ".join(why),
                        })

            # Attach ResourceQuota (hard/used) per namespace, if present.
            try:
                quotas = (
                    self._core.list_namespaced_resource_quota(namespace).items if namespace
                    else self._core.list_resource_quota_for_all_namespaces().items
                )
                for q in quotas:
                    a = agg.get(q.metadata.namespace)
                    if a is not None:
                        a.setdefault("quota", {})[q.metadata.name] = {
                            "hard": dict(q.status.hard or {}) if q.status else {},
                            "used": dict(q.status.used or {}) if q.status else {},
                        }
            except ApiException:
                pass

            rows = sorted(agg.values(), key=lambda r: r["namespace"])
            return {"rows": rows, "risks": risks, "risk_total": len(risks)}

        key = f"budget:{namespace or '*'}"
        return self._cached(key, 8.0, lambda: self._guard("namespace budget", _do))

    def traffic_graph(self, namespace: str | None = None) -> dict:
        """Traffic topology for the cluster map: Ingress → Service → Workload →
        Pods. Returns ``{nodes:[{id,kind,name,namespace,...}], edges:[{from,to}]}``
        (cached ~8s). Edges: ingress→service (rule backends), service→pod (selector
        match), workload→pod (ownerReferences; Pod→ReplicaSet→Deployment chain)."""

        def _do():
            ns = namespace
            ings = (self._net.list_namespaced_ingress(ns).items if ns
                    else self._net.list_ingress_for_all_namespaces().items)
            svcs = (self._core.list_namespaced_service(ns).items if ns
                    else self._core.list_service_for_all_namespaces().items)
            pods = (self._core.list_namespaced_pod(ns).items if ns
                    else self._core.list_pod_for_all_namespaces().items)
            rss = (self._apps.list_namespaced_replica_set(ns).items if ns
                   else self._apps.list_replica_set_for_all_namespaces().items)

            nodes: dict[str, dict] = {}
            edges: list[dict] = []

            def add(kind, name, nsx, **extra):
                nid = f"{kind}/{nsx}/{name}"
                if nid not in nodes:
                    nodes[nid] = {"id": nid, "kind": kind, "name": name, "namespace": nsx, **extra}
                return nid

            # ReplicaSet -> owning Deployment (to collapse pods onto the Deployment).
            rs_owner = {}
            for rs in rss:
                for o in (rs.metadata.owner_references or []):
                    if o.kind == "Deployment":
                        rs_owner[(rs.metadata.namespace, rs.metadata.name)] = o.name

            def pod_workload(p):
                for o in (p.metadata.owner_references or []):
                    if o.kind == "ReplicaSet":
                        dep = rs_owner.get((p.metadata.namespace, o.name))
                        return ("Deployment", dep) if dep else ("ReplicaSet", o.name)
                    if o.kind in ("StatefulSet", "DaemonSet", "Job"):
                        return (o.kind, o.name)
                return None

            pod_index = {}  # (ns,name) -> (pod_id, pod)
            for p in pods:
                pid = add("pod", p.metadata.name, p.metadata.namespace,
                          status=(p.status.phase if p.status else None))
                pod_index[(p.metadata.namespace, p.metadata.name)] = (pid, p)
                wl = pod_workload(p)
                if wl and wl[1]:
                    wid = add("workload", wl[1], p.metadata.namespace, subkind=wl[0])
                    edges.append({"from": wid, "to": pid})

            for s in svcs:
                sel = s.spec.selector or {}
                if not sel:
                    continue
                sid = add("service", s.metadata.name, s.metadata.namespace, svc_type=s.spec.type)
                for (pns, _pn), (pid, p) in pod_index.items():
                    if pns != s.metadata.namespace:
                        continue
                    labels = p.metadata.labels or {}
                    if all(labels.get(k) == v for k, v in sel.items()):
                        edges.append({"from": sid, "to": pid})

            for ing in ings:
                iid = add("ingress", ing.metadata.name, ing.metadata.namespace)
                backends = set()
                db = ing.spec.default_backend
                if db and db.service:
                    backends.add(db.service.name)
                for r in (ing.spec.rules or []):
                    if r.http:
                        for path in (r.http.paths or []):
                            if path.backend and path.backend.service:
                                backends.add(path.backend.service.name)
                for bn in backends:
                    sid = add("service", bn, ing.metadata.namespace)  # ensure node exists
                    edges.append({"from": iid, "to": sid})

            return {"nodes": list(nodes.values()), "edges": edges}

        key = f"graph:{namespace or '*'}"
        return self._cached(key, 8.0, lambda: self._guard("traffic graph", _do))

    # --- node management (cordon / uncordon / drain) ---------------------

    def cordon_node(self, name: str, unschedulable: bool = True) -> None:
        """Mark a node (un)schedulable — like ``kubectl cordon``/``uncordon``."""
        self._guard(
            ("cordon" if unschedulable else "uncordon") + " node",
            lambda: self._core.patch_node(name, {"spec": {"unschedulable": unschedulable}}),
        )

    def drain_node(self, name: str, grace_period: int | None = None) -> dict:
        """Cordon a node then evict its pods — like ``kubectl drain``.

        Skips DaemonSet-managed and static/mirror pods (they can't be evicted),
        and uses the Eviction API so PodDisruptionBudgets are honoured. Returns
        ``{cordoned, evicted, skipped:[{pod,namespace,reason}], total}``.
        """
        from kubernetes import client as kc

        def _do():
            self._core.patch_node(name, {"spec": {"unschedulable": True}})
            pods = self._core.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={name}"
            ).items
            evicted = 0
            skipped: list[dict] = []
            for p in pods:
                pn, pns = p.metadata.name, p.metadata.namespace
                ann = p.metadata.annotations or {}
                kinds = {r.kind for r in (p.metadata.owner_references or [])}
                if "DaemonSet" in kinds:
                    skipped.append({"pod": pn, "namespace": pns, "reason": "DaemonSet"})
                    continue
                if "kubernetes.io/config.mirror" in ann:
                    skipped.append({"pod": pn, "namespace": pns, "reason": "estático/mirror"})
                    continue
                body = kc.V1Eviction(
                    metadata=kc.V1ObjectMeta(name=pn, namespace=pns),
                    delete_options=(
                        kc.V1DeleteOptions(grace_period_seconds=grace_period)
                        if grace_period is not None
                        else None
                    ),
                )
                # 429 from the Eviction API means evicting now would violate a
                # PodDisruptionBudget — a transient signal to back off and retry
                # (what `kubectl drain` does), not a terminal failure.
                deadline = time.monotonic() + 120
                while True:
                    try:
                        self._core.create_namespaced_pod_eviction(pn, pns, body)
                        evicted += 1
                        break
                    except ApiException as exc:
                        if exc.status == 429 and time.monotonic() < deadline:
                            time.sleep(2)
                            continue
                        reason = "bloqueado por PodDisruptionBudget (timeout)" if exc.status == 429 else exc.reason
                        skipped.append({"pod": pn, "namespace": pns, "reason": reason})
                        break
            return {"cordoned": True, "evicted": evicted, "skipped": skipped, "total": len(pods)}

        return self._guard("drain node", _do)

    # --- rollout management (deployments) --------------------------------

    def rollout_pause(self, kind: str, name: str, namespace: str, paused: bool) -> None:
        """Pause/resume a deployment rollout (``.spec.paused``)."""
        if kind != "deployments":
            raise KubernetesError("pausar/retomar rollout só é suportado em deployments")
        self._guard(
            "pause rollout",
            lambda: self._apps.patch_namespaced_deployment(
                name, namespace, {"spec": {"paused": paused}}
            ),
        )

    def _deployment_replicasets(self, name: str, namespace: str):
        """ReplicaSets owned by a deployment (its revisions)."""
        dep = self._apps.read_namespaced_deployment(name, namespace)
        sel = (dep.spec.selector.match_labels or {}) if dep.spec.selector else {}
        label = ",".join(f"{k}={v}" for k, v in sel.items())
        rss = self._apps.list_namespaced_replica_set(
            namespace, label_selector=label or None
        ).items
        return [
            rs
            for rs in rss
            if any(
                r.kind == "Deployment" and r.name == name
                for r in (rs.metadata.owner_references or [])
            )
        ]

    def rollout_history(self, kind: str, name: str, namespace: str) -> list[dict]:
        """Revision history of a deployment (its ReplicaSets), newest first."""
        if kind != "deployments":
            raise KubernetesError("histórico de rollout só é suportado em deployments")

        def _do():
            out = []
            for rs in self._deployment_replicasets(name, namespace):
                rev = (rs.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision"
                )
                conts = (rs.spec.template.spec.containers or []) if rs.spec.template else []
                out.append({
                    "revision": int(rev) if rev and rev.isdigit() else None,
                    "name": rs.metadata.name,
                    "created": rs.metadata.creation_timestamp.isoformat()
                    if rs.metadata.creation_timestamp else None,
                    "replicas": rs.status.replicas or 0,
                    "images": [c.image for c in conts],
                    "current": (rs.status.replicas or 0) > 0,
                })
            out.sort(key=lambda r: (r["revision"] is None, -(r["revision"] or 0)))
            return out

        return self._guard("rollout history", _do)

    def rollout_undo(self, kind: str, name: str, namespace: str, revision: int) -> None:
        """Roll a deployment back to a revision's pod template (kubectl rollout undo)."""
        if kind != "deployments":
            raise KubernetesError("rollback só é suportado em deployments")

        def _do():
            target = None
            for rs in self._deployment_replicasets(name, namespace):
                rev = (rs.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision"
                )
                if rev and rev.isdigit() and int(rev) == revision:
                    target = rs
                    break
            if target is None:
                raise KubernetesError(f"revisão {revision} não encontrada")
            tmpl = self._api_client.sanitize_for_serialization(target.spec.template)
            labels = (tmpl.get("metadata") or {}).get("labels") or {}
            labels.pop("pod-template-hash", None)  # don't pin the old RS's hash
            self._apps.patch_namespaced_deployment(
                name, namespace, {"spec": {"template": tmpl}}
            )

        self._guard("rollout undo", _do)

    # --- cluster diagnostics ("Problemas") -------------------------------

    def cluster_issues(self) -> dict:
        """Scan the cluster for problems: not-ready/crashlooping/OOMKilled/pending
        pods, unavailable workloads, failed jobs, unbound PVCs, unhealthy/cordoned
        nodes. Returns ``{issues:[...], counts:{critical,warning}, total}``; cached
        briefly. Each issue links to its object (kind/name/namespace)."""

        def _do():
            issues: list[dict] = []

            def add(severity, category, kind, name, ns, reason, detail):
                issues.append({
                    "severity": severity, "category": category, "kind": kind,
                    "name": name, "namespace": ns, "reason": reason, "detail": detail,
                })

            # Nodes
            for n in self._core.list_node().items:
                conds = {c.type: c for c in (n.status.conditions or [])}
                ready = conds.get("Ready")
                if not ready or ready.status != "True":
                    add("critical", "Nodes", "nodes", n.metadata.name, None, "NotReady",
                        (ready.message if ready else "sem condição Ready"))
                for t in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"):
                    c = conds.get(t)
                    if c and c.status == "True":
                        add("warning", "Nodes", "nodes", n.metadata.name, None, t, c.message or "")
                if n.spec and n.spec.unschedulable:
                    add("warning", "Nodes", "nodes", n.metadata.name, None,
                        "SchedulingDisabled", "node cordonado")

            # Pods
            for p in self._core.list_pod_for_all_namespaces().items:
                nm, ns = p.metadata.name, p.metadata.namespace
                st = p.status
                phase = st.phase if st else None
                if phase == "Failed":
                    add("critical", "Pods", "pods", nm, ns, "Failed", st.reason or "")
                elif phase == "Pending":
                    add("warning", "Pods", "pods", nm, ns, "Pending", st.reason or "")
                for cs in (st.container_statuses if st else None) or []:
                    w = cs.state.waiting if cs.state else None
                    last = cs.last_state.terminated if cs.last_state else None
                    if w and w.reason in (
                        "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                        "CreateContainerConfigError", "CreateContainerError", "InvalidImageName",
                    ):
                        sev = "critical" if "CrashLoop" in w.reason else "warning"
                        add(sev, "Pods", "pods", nm, ns, w.reason, f"{cs.name}: {w.message or ''}".strip())
                    if last and last.reason == "OOMKilled":
                        add("warning", "Pods", "pods", nm, ns, "OOMKilled", f"container {cs.name}")
                    if (cs.restart_count or 0) >= 5:
                        add("warning", "Pods", "pods", nm, ns, "HighRestarts",
                            f"{cs.restart_count} restarts ({cs.name})")

            # Deployments / StatefulSets / DaemonSets
            for d in self._apps.list_deployment_for_all_namespaces().items:
                desired, avail = d.spec.replicas or 0, d.status.available_replicas or 0
                if avail < desired:
                    add("warning", "Workloads", "deployments", d.metadata.name,
                        d.metadata.namespace, "NotAvailable", f"{avail}/{desired} disponíveis")
            for s in self._apps.list_stateful_set_for_all_namespaces().items:
                desired, ready = s.spec.replicas or 0, s.status.ready_replicas or 0
                if ready < desired:
                    add("warning", "Workloads", "statefulsets", s.metadata.name,
                        s.metadata.namespace, "NotReady", f"{ready}/{desired} prontos")
            for ds in self._apps.list_daemon_set_for_all_namespaces().items:
                desired, ready = ds.status.desired_number_scheduled or 0, ds.status.number_ready or 0
                if ready < desired:
                    add("warning", "Workloads", "daemonsets", ds.metadata.name,
                        ds.metadata.namespace, "NotReady", f"{ready}/{desired} prontos")

            # Jobs
            for j in self._batch.list_job_for_all_namespaces().items:
                if j.status and j.status.failed:
                    add("critical", "Jobs", "jobs", j.metadata.name, j.metadata.namespace,
                        "Failed", f"{j.status.failed} falha(s)")

            # PVCs
            for pvc in self._core.list_persistent_volume_claim_for_all_namespaces().items:
                ph = pvc.status.phase if pvc.status else None
                if ph and ph != "Bound":
                    add("warning", "Storage", "pvc", pvc.metadata.name,
                        pvc.metadata.namespace, ph, "não vinculado")

            counts = {"critical": 0, "warning": 0}
            for i in issues:
                counts[i["severity"]] = counts.get(i["severity"], 0) + 1
            order = {"critical": 0, "warning": 1}
            issues.sort(key=lambda i: (
                order.get(i["severity"], 2), i["category"], i["namespace"] or "", i["name"],
            ))
            return {"issues": issues, "counts": counts, "total": len(issues)}

        return self._cached("issues", 8.0, lambda: self._guard("scan issues", _do))

    def delete_resource(self, kind: str, name: str, namespace: str | None = None):
        from app.kubernetes.resources import MANIFEST_KINDS

        api_version, k8s_kind, namespaced = MANIFEST_KINDS[kind]

        def _do():
            res = self._dyn().resources.get(api_version=api_version, kind=k8s_kind)
            res.delete(name=name, namespace=namespace if namespaced else None)

        self._guard(f"delete {kind}", _do)

    def validate_manifests(
        self, yaml_text: str, default_namespace: str = "default"
    ) -> list[dict]:
        """Server-side dry-run each document — validates schema/admission without
        creating anything. Returns a per-document valid/error status."""
        try:
            docs = [d for d in yaml.safe_load_all(yaml_text) if isinstance(d, dict)]
        except yaml.YAMLError as exc:
            raise KubernetesError(f"YAML inválido: {exc}") from exc
        if not docs:
            raise KubernetesError("Nenhum documento YAML válido encontrado")

        results: list[dict] = []
        for doc in docs:
            meta = doc.get("metadata") or {}
            entry = {
                "kind": doc.get("kind", "?"),
                "name": meta.get("name", "?"),
                "namespace": meta.get("namespace") or default_namespace,
            }
            api_version, kind = doc.get("apiVersion"), doc.get("kind")
            if not api_version or not kind:
                results.append({**entry, "status": "error", "message": "apiVersion/kind ausente"})
                continue
            try:
                res = self._dyn().resources.get(api_version=api_version, kind=kind)
                ns = entry["namespace"] if res.namespaced else None
                res.create(body=doc, namespace=ns, dry_run="All")
                results.append({**entry, "status": "valid"})
            except ApiException as exc:
                results.append({**entry, "status": "error", "message": _api_errors_msg([exc])})
            except Exception as exc:  # noqa: BLE001
                results.append({**entry, "status": "error", "message": str(exc)})
        return results

    def _parse_docs(self, yaml_text: str) -> list[dict]:
        try:
            docs = [d for d in yaml.safe_load_all(yaml_text) if isinstance(d, dict)]
        except yaml.YAMLError as exc:
            raise KubernetesError(f"YAML inválido: {exc}") from exc
        if not docs:
            raise KubernetesError("Nenhum documento YAML válido encontrado")
        return docs

    def apply_documents(
        self, yaml_text: str, default_namespace: str = "default",
        field_manager: str = "lensfy",
    ) -> list[dict]:
        """Server-side apply each document — create-or-update, like
        ``kubectl apply --server-side --force-conflicts``. Unlike
        :meth:`deploy_manifests` (create-only), re-applying an existing resource
        updates it. Returns per-document status: created | configured | error.
        """
        from kubernetes.dynamic.exceptions import NotFoundError

        results: list[dict] = []
        for doc in self._parse_docs(yaml_text):
            meta = doc.get("metadata") or {}
            entry = {
                "kind": doc.get("kind", "?"), "name": meta.get("name", "?"),
                "namespace": meta.get("namespace") or default_namespace,
            }
            if not doc.get("apiVersion") or not doc.get("kind") or not meta.get("name"):
                results.append({**entry, "status": "error", "message": "apiVersion/kind/name ausente"})
                continue
            try:
                res = self._dyn().resources.get(
                    api_version=doc["apiVersion"], kind=doc["kind"]
                )
                ns = entry["namespace"] if res.namespaced else None
                existed = True
                try:
                    res.get(name=entry["name"], namespace=ns)
                except NotFoundError:
                    existed = False
                self._dyn().server_side_apply(
                    res, body=doc, name=entry["name"], namespace=ns,
                    field_manager=field_manager, force_conflicts=True,
                )
                results.append({**entry, "status": "configured" if existed else "created"})
            except Exception as exc:  # noqa: BLE001
                results.append({**entry, "status": "error", "message": _dyn_err_msg(exc)})
        return results

    def diff_documents(
        self, yaml_text: str, default_namespace: str = "default",
        field_manager: str = "lensfy",
    ) -> list[dict]:
        """Preview a deploy: server-side **dry-run** apply each document and diff
        the merged result against the live object (like ``kubectl diff``).

        Returns per-document ``{kind, name, namespace, action, changes}`` where
        action is create | update | unchanged | error and changes is a list of
        ``{path, old, new}`` leaf differences (bounded).
        """
        from kubernetes.dynamic.exceptions import NotFoundError

        results: list[dict] = []
        for doc in self._parse_docs(yaml_text):
            meta = doc.get("metadata") or {}
            entry = {
                "kind": doc.get("kind", "?"), "name": meta.get("name", "?"),
                "namespace": meta.get("namespace") or default_namespace,
            }
            if not doc.get("apiVersion") or not doc.get("kind") or not meta.get("name"):
                results.append({**entry, "action": "error", "message": "apiVersion/kind/name ausente"})
                continue
            try:
                res = self._dyn().resources.get(
                    api_version=doc["apiVersion"], kind=doc["kind"]
                )
                ns = entry["namespace"] if res.namespaced else None
                try:
                    live = res.get(name=entry["name"], namespace=ns).to_dict()
                except NotFoundError:
                    live = None
                if live is None:
                    results.append({**entry, "action": "create", "changes": []})
                    continue
                merged = self._dyn().server_side_apply(
                    res, body=doc, name=entry["name"], namespace=ns,
                    field_manager=field_manager, force_conflicts=True, dry_run="All",
                ).to_dict()
                changes = _diff_dicts(_clean_for_diff(live), _clean_for_diff(merged))
                results.append({
                    **entry, "action": "update" if changes else "unchanged",
                    "changes": changes[:200],
                })
            except Exception as exc:  # noqa: BLE001
                results.append({**entry, "action": "error", "message": _dyn_err_msg(exc)})
        return results

    # --- node shell (privileged pod + nsenter into the host) -------------

    def node_shell_exec(self, node: str):
        """Open a host shell on a node, Lens-style.

        Kubernetes has no node exec, so we schedule a privileged pod on the node
        with hostPID and exec ``nsenter -t 1`` into the host namespaces. Returns
        ``(exec_ws, pod_name, namespace)``; the caller bridges exec_ws and must
        delete the pod (``delete_pod_quiet``) when done.
        """
        ns, pod = self._create_node_shell(node)
        try:
            exec_ws = self.exec_shell(pod, ns, container="shell", command=_NODE_SHELL_CMD)
        except Exception:
            self.delete_pod_quiet(pod, ns)
            raise
        return exec_ws, pod, ns

    def _create_node_shell(self, node: str) -> tuple[str, str]:
        import time
        import uuid

        safe = re.sub(r"[^a-z0-9-]+", "-", node.lower()).strip("-")[:40]
        pod = f"lensfy-node-shell-{safe}-{uuid.uuid4().hex[:5]}"
        ns = _NODE_SHELL_NS
        body = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod, namespace=ns, labels={"app": "lensfy-node-shell"}
            ),
            spec=client.V1PodSpec(
                node_name=node,
                host_pid=True,
                restart_policy="Never",
                tolerations=[client.V1Toleration(operator="Exists")],
                containers=[
                    client.V1Container(
                        name="shell",
                        image=_NODE_SHELL_IMAGE,
                        command=["sleep", "infinity"],
                        stdin=True,
                        tty=True,
                        security_context=client.V1SecurityContext(privileged=True),
                    )
                ],
            ),
        )
        try:
            self._core.create_namespaced_pod(ns, body)
        except ApiException as exc:
            detail = exc.reason
            try:
                detail = (json.loads(exc.body or "{}").get("message")) or detail
            except Exception:  # noqa: BLE001
                pass
            hint = ""
            if exc.status in (403, 422) and "privileg" in (detail or "").lower():
                hint = (
                    " — o cluster restringe pods privilegiados (Pod Security). O node-shell "
                    "exige um pod privileged + hostPID em kube-system."
                )
            raise KubernetesError(
                f"não foi possível criar o pod de node-shell: {detail}{hint}"
            ) from exc

        # The pod skips the scheduler (nodeName is set) so it's Pending only while
        # the image pulls / container is created. Surface a stuck reason instead of
        # silently waiting 45s.
        deadline = time.time() + 45
        last = ""
        while time.time() < deadline:
            p = self._guard(
                "read node-shell pod", lambda: self._core.read_namespaced_pod(pod, ns)
            )
            phase = p.status.phase if p.status else None
            if phase == "Running":
                return ns, pod
            if phase in ("Failed", "Succeeded"):
                self.delete_pod_quiet(pod, ns)
                raise KubernetesError(
                    f"node-shell pod entrou em estado {phase}: {p.status.reason or ''}".strip()
                )
            for cs in (p.status.container_statuses if p.status else None) or []:
                w = cs.state.waiting if cs.state else None
                if w and w.reason:
                    last = f"{w.reason}: {w.message or ''}".strip(": ").strip()
                    if w.reason in (
                        "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
                        "CreateContainerConfigError", "CreateContainerError",
                    ):
                        self.delete_pod_quiet(pod, ns)
                        raise KubernetesError(f"node-shell pod não iniciou — {last}")
            time.sleep(0.6)
        self.delete_pod_quiet(pod, ns)
        raise KubernetesError(
            "timeout aguardando o pod de node-shell ficar Running"
            + (f" (último estado: {last})" if last else "")
        )

    def delete_pod_quiet(self, name: str, namespace: str) -> None:
        try:
            self._core.delete_namespaced_pod(
                name=name, namespace=namespace, grace_period_seconds=0
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("falha ao remover pod %s/%s: %s", namespace, name, exc)

    # --- RBAC (quem pode o quê + simulador can-i) ------------------------

    def rbac_subjects(self) -> dict:
        """Aggregate RBAC: every subject (User/Group/ServiceAccount) and the
        roles bound to it (Role/ClusterRoleBindings), with the union of the
        verbs/resources those roles grant. Backs the "quem pode o quê" view.
        Cluster-admin (``*`` verbs on ``*`` resources cluster-wide) is flagged.
        """

        def _do():
            croles = {cr.metadata.name: cr for cr in self._rbac.list_cluster_role().items}
            roles = {
                (r.metadata.namespace, r.metadata.name): r
                for r in self._rbac.list_role_for_all_namespaces().items
            }

            def rules_of(role_ref, binding_ns):
                if role_ref.kind == "ClusterRole":
                    cr = croles.get(role_ref.name)
                    return cr.rules or [] if cr else []
                r = roles.get((binding_ns, role_ref.name))
                return r.rules or [] if r else []

            subjects: dict[str, dict] = {}

            def add(subj, role_ref, scope, binding_ns):
                key = f"{subj.kind}/{getattr(subj, 'namespace', None) or ''}/{subj.name}"
                s = subjects.setdefault(key, {
                    "kind": subj.kind, "name": subj.name,
                    "namespace": getattr(subj, "namespace", None),
                    "bindings": [], "verbs": set(), "resources": set(), "cluster_wide": False,
                })
                s["bindings"].append({
                    "role": f"{role_ref.kind}/{role_ref.name}", "scope": scope,
                })
                if scope == "cluster":
                    s["cluster_wide"] = True
                for rule in rules_of(role_ref, binding_ns):
                    s["verbs"].update(rule.verbs or [])
                    s["resources"].update(rule.resources or [])

            for crb in self._rbac.list_cluster_role_binding().items:
                for subj in (crb.subjects or []):
                    add(subj, crb.role_ref, "cluster", None)
            for rb in self._rbac.list_role_binding_for_all_namespaces().items:
                for subj in (rb.subjects or []):
                    add(subj, rb.role_ref, rb.metadata.namespace, rb.metadata.namespace)

            rows = []
            for s in subjects.values():
                admin = s["cluster_wide"] and "*" in s["verbs"] and "*" in s["resources"]
                rows.append({
                    "kind": s["kind"], "name": s["name"], "namespace": s["namespace"],
                    "binding_count": len(s["bindings"]),
                    "bindings": s["bindings"][:50],
                    "verbs": sorted(s["verbs"]),
                    "resources": sorted(s["resources"])[:60],
                    "cluster_admin": admin,
                })
            rows.sort(key=lambda r: (not r["cluster_admin"], r["kind"], r["name"]))
            admins = sum(1 for r in rows if r["cluster_admin"])
            return {"subjects": rows, "total": len(rows), "cluster_admins": admins}

        return self._cached("rbac_subjects", 15.0, lambda: self._guard("rbac subjects", _do))

    def rbac_can_i(
        self, verb: str, resource: str, namespace: str | None = None,
        group: str = "", subresource: str | None = None, name: str | None = None,
        user: str | None = None, groups: list[str] | None = None,
        serviceaccount: str | None = None,
    ) -> dict:
        """Authoritative permission check via the SubjectAccessReview API.

        With no subject, reviews the current credential (SelfSubjectAccessReview,
        like ``kubectl auth can-i``). With a user/groups/serviceaccount, reviews
        that subject (SubjectAccessReview — needs RBAC to create reviews).
        """
        from kubernetes import client as kc

        auth = kc.AuthorizationV1Api(self._api_client)
        attrs = kc.V1ResourceAttributes(
            verb=verb, resource=resource, namespace=namespace,
            group=group or None, subresource=subresource, name=name,
        )

        def _do():
            if user or groups or serviceaccount:
                sub_user = user
                if serviceaccount and not sub_user:
                    sub_user = (
                        serviceaccount
                        if serviceaccount.startswith("system:serviceaccount:")
                        else f"system:serviceaccount:{namespace or 'default'}:{serviceaccount}"
                    )
                review = kc.V1SubjectAccessReview(
                    spec=kc.V1SubjectAccessReviewSpec(
                        resource_attributes=attrs,
                        user=sub_user, groups=list(groups) if groups else None,
                    )
                )
                res = auth.create_subject_access_review(review)
                who = sub_user or "subject"
            else:
                review = kc.V1SelfSubjectAccessReview(
                    spec=kc.V1SelfSubjectAccessReviewSpec(resource_attributes=attrs)
                )
                res = auth.create_self_subject_access_review(review)
                who = "(credencial atual)"
            st = res.status
            return {
                "allowed": bool(st.allowed),
                "denied": bool(getattr(st, "denied", False)),
                "reason": st.reason or "",
                "subject": who,
            }

        return self._guard("access review", _do)

    # --- security posture scan (PSS-style) -------------------------------

    def security_scan(self, namespace: str | None = None) -> dict:
        """Scan running pods for risky security settings and surface findings.

        Inspired by the Pod Security Standards: privileged/host namespaces,
        hostPath volumes, missing runAsNonRoot, dangerous capabilities, writable
        root fs, missing resource limits, mutable image tags, auto-mounted SA
        tokens. Returns ``{findings, counts, total, score, scanned}`` (score 0-100,
        higher is safer). Cached briefly.
        """

        DANGEROUS_CAPS = {"SYS_ADMIN", "NET_ADMIN", "NET_RAW", "SYS_PTRACE", "ALL"}

        def _do():
            pods = (
                self._core.list_namespaced_pod(namespace).items if namespace
                else self._core.list_pod_for_all_namespaces().items
            )
            findings: list[dict] = []

            def add(sev, ns, pod, container, rule, detail):
                findings.append({
                    "severity": sev, "namespace": ns, "pod": pod,
                    "container": container, "rule": rule, "detail": detail,
                })

            for p in pods:
                ns, nm = p.metadata.namespace, p.metadata.name
                spec = p.spec
                psc = spec.security_context
                if spec.host_network:
                    add("critical", ns, nm, None, "hostNetwork", "usa a rede do host")
                if spec.host_pid:
                    add("critical", ns, nm, None, "hostPID", "compartilha o PID namespace do host")
                if spec.host_ipc:
                    add("critical", ns, nm, None, "hostIPC", "compartilha o IPC namespace do host")
                for vol in (spec.volumes or []):
                    if vol.host_path:
                        add("warning", ns, nm, None, "hostPath",
                            f"volume {vol.name} monta {vol.host_path.path} do host")
                pod_nonroot = bool(psc and (psc.run_as_non_root or (psc.run_as_user and psc.run_as_user != 0)))
                automount = spec.automount_service_account_token
                if automount is None or automount:
                    add("info", ns, nm, None, "saToken",
                        "token da ServiceAccount montado automaticamente")
                for c in (spec.containers or []):
                    sc = c.security_context
                    if sc and sc.privileged:
                        add("critical", ns, nm, c.name, "privileged", "container privilegiado")
                    if sc and sc.allow_privilege_escalation:
                        add("warning", ns, nm, c.name, "allowPrivilegeEscalation",
                            "permite escalonamento de privilégio")
                    c_nonroot = bool(sc and (sc.run_as_non_root or (sc.run_as_user and sc.run_as_user != 0)))
                    if not (c_nonroot or pod_nonroot):
                        add("warning", ns, nm, c.name, "runAsRoot",
                            "pode rodar como root (runAsNonRoot não definido)")
                    caps = sc.capabilities if sc else None
                    if caps and caps.add:
                        bad = {x.upper() for x in caps.add} & DANGEROUS_CAPS
                        if bad:
                            add("warning", ns, nm, c.name, "capabilities",
                                "adiciona capabilities perigosas: " + ", ".join(sorted(bad)))
                    if not (sc and sc.read_only_root_filesystem):
                        add("info", ns, nm, c.name, "readOnlyRootFilesystem",
                            "filesystem raiz é gravável")
                    res = c.resources
                    lim = (res.limits or {}) if res else {}
                    if not (lim.get("cpu") or lim.get("memory")):
                        add("warning", ns, nm, c.name, "noLimits", "sem limits de recursos")
                    img = c.image or ""
                    tag = img.rsplit("/", 1)[-1]
                    if img.endswith(":latest") or ":" not in tag:
                        add("info", ns, nm, c.name, "mutableTag",
                            f"imagem sem tag fixa: {img or '(?)'}")

            counts: dict[str, int] = {}
            for f in findings:
                counts[f["severity"]] = counts.get(f["severity"], 0) + 1
            order = {"critical": 0, "warning": 1, "info": 2}
            findings.sort(key=lambda f: (
                order.get(f["severity"], 3), f["namespace"] or "", f["pod"] or "",
            ))
            score = max(0, 100 - counts.get("critical", 0) * 10
                        - counts.get("warning", 0) * 3 - counts.get("info", 0))
            return {
                "findings": findings, "counts": counts, "total": len(findings),
                "score": score, "scanned": len(pods),
            }

        key = f"secscan:{namespace or '*'}"
        return self._cached(key, 8.0, lambda: self._guard("security scan", _do))

    # --- CRDs / custom resources (descoberta dinâmica) -------------------

    def list_crds(self) -> dict:
        """List CustomResourceDefinitions installed in the cluster."""
        from kubernetes import client as kc

        api = kc.ApiextensionsV1Api(self._api_client)

        def _do():
            rows = []
            for crd in api.list_custom_resource_definition().items:
                spec = crd.spec
                served = [v.name for v in (spec.versions or []) if v.served]
                storage = next(
                    (v.name for v in (spec.versions or []) if v.storage),
                    served[0] if served else None,
                )
                rows.append({
                    "name": crd.metadata.name,
                    "group": spec.group,
                    "kind": spec.names.kind,
                    "plural": spec.names.plural,
                    "scope": spec.scope,  # "Namespaced" | "Cluster"
                    "versions": served,
                    "version": storage,
                    "age": _short_age(crd.metadata.creation_timestamp),
                })
            rows.sort(key=lambda r: (r["group"] or "", r["kind"]))
            return {"rows": rows, "total": len(rows)}

        return self._cached("crds", 30.0, lambda: self._guard("list CRDs", _do))

    def list_custom_resource(
        self, group: str, version: str, plural: str, namespace: str | None = None
    ) -> dict:
        """List instances of a custom resource as a generic name/namespace/age table."""
        from kubernetes import client as kc

        api = kc.CustomObjectsApi(self._api_client)

        def _do():
            if namespace:
                data = api.list_namespaced_custom_object(group, version, namespace, plural)
            else:
                data = api.list_cluster_custom_object(group, version, plural)
            rows = []
            for it in data.get("items", []):
                meta = it.get("metadata", {})
                rows.append({
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "age": _short_age(_parse_ts(meta.get("creationTimestamp"))),
                })
            rows.sort(key=lambda r: (r.get("namespace") or "", r.get("name") or ""))
            return {"group": group, "version": version, "plural": plural,
                    "rows": rows, "total": len(rows)}

        return self._guard(f"list {plural}", _do)

    def get_custom_resource(
        self, group: str, version: str, plural: str, name: str,
        namespace: str | None = None,
    ) -> str:
        """Fetch a single custom resource instance as YAML."""
        from kubernetes import client as kc

        api = kc.CustomObjectsApi(self._api_client)

        def _do():
            if namespace:
                obj = api.get_namespaced_custom_object(group, version, namespace, plural, name)
            else:
                obj = api.get_cluster_custom_object(group, version, plural, name)
            obj.get("metadata", {}).pop("managedFields", None)
            return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, width=4096)

        return self._guard(f"get {plural}/{name}", _do)

    # --- API discovery (todos os tipos de recurso, incl. CRDs) -----------

    def discover_resources(self) -> dict:
        """Enumerate every listable resource type served by the cluster — built-in
        AND installed CRDs (Istio Gateway/VirtualService, Gateway API, cert-manager,
        ArgoCD, …) — grouped by API group. Backs the dynamic Explorer tree.

        Returns ``{groups:[{group, resources:[{kind,name,apiVersion,group,
        namespaced,shortNames,preferred}]}], total}`` (one entry per group/kind,
        preferring the served/preferred version). Cached briefly.
        """

        def _do():
            best: dict[tuple, dict] = {}
            for r in self._dyn().resources.search():
                name = getattr(r, "name", "") or ""
                if not name or "/" in name:  # pula subrecursos (pods/status, …)
                    continue
                verbs = getattr(r, "verbs", None) or []
                if "list" not in verbs:  # só o que dá para listar
                    continue
                kind = getattr(r, "kind", None)
                if not kind or kind.endswith("List"):
                    # ``*List`` são os tipos-coleção (ServiceEntryList, …) que a
                    # descoberta retorna junto do recurso real — não são listáveis
                    # por kind no dynamic client (dariam 502). Ignora.
                    continue
                group = getattr(r, "group", "") or ""
                entry = {
                    "kind": kind, "name": name,
                    "apiVersion": r.group_version,  # "grupo/versão" ou "v1"
                    "group": group,
                    "namespaced": bool(getattr(r, "namespaced", False)),
                    "shortNames": list(getattr(r, "short_names", None) or []),
                    "preferred": bool(getattr(r, "preferred", False)),
                }
                key = (group, kind)
                cur = best.get(key)
                if cur is None or (entry["preferred"] and not cur["preferred"]):
                    best[key] = entry

            grouped: dict[str, list] = {}
            for entry in best.values():
                grouped.setdefault(entry["group"], []).append(entry)
            groups = [
                {"group": g, "resources": sorted(items, key=lambda e: e["kind"])}
                for g, items in sorted(grouped.items())
            ]
            return {"groups": groups, "total": len(best)}

        return self._cached("discovery", 60.0, lambda: self._guard("discover resources", _do))

    def list_resource_dynamic(
        self, api_version: str, kind: str, namespace: str | None = None
    ) -> dict:
        """Generic list (name/namespace/age) of ANY resource by apiVersion+kind,
        via the dynamic client — works for built-ins and CRDs alike."""

        def _do():
            res = self._dyn().resources.get(api_version=api_version, kind=kind)
            ns = namespace if (namespace and res.namespaced) else None
            data = res.get(namespace=ns).to_dict()
            rows = []
            for it in data.get("items", []):
                meta = it.get("metadata", {}) or {}
                rows.append({
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "age": _short_age(_parse_ts(meta.get("creationTimestamp"))),
                })
            rows.sort(key=lambda r: (r.get("namespace") or "", r.get("name") or ""))
            return {"apiVersion": api_version, "kind": kind,
                    "namespaced": res.namespaced, "rows": rows, "total": len(rows)}

        return self._guard(f"list {kind}", _do)

    def get_manifest_dynamic(
        self, api_version: str, kind: str, name: str, namespace: str | None = None
    ) -> str:
        """Fetch any resource (built-in or CRD) as YAML by apiVersion+kind."""

        def _do():
            res = self._dyn().resources.get(api_version=api_version, kind=kind)
            ns = namespace if res.namespaced else None
            return self._to_yaml(res.get(name=name, namespace=ns).to_dict())

        return self._guard(f"get {kind}/{name}", _do)

    # --- capacity & rightsizing ------------------------------------------

    def capacity(self) -> dict:
        """Per-node allocatable vs requested vs live usage — the scheduling
        headroom view (like the 'Allocated resources' in ``kubectl describe
        node``, cluster-wide). ``totals.metrics_available`` is False when
        metrics-server is absent (usage columns are then null)."""

        def _do():
            nodes = self._core.list_node().items
            pods = self._core.list_pod_for_all_namespaces().items

            req: dict[str, dict] = {}
            for p in pods:
                if p.status and p.status.phase in ("Succeeded", "Failed"):
                    continue
                node = p.spec.node_name
                if not node:
                    continue
                a = req.setdefault(node, {"cpu": 0, "mem": 0, "pods": 0})
                a["pods"] += 1
                for c in (p.spec.containers or []):
                    r = (c.resources.requests or {}) if c.resources else {}
                    a["cpu"] += _cpu_milli(r.get("cpu"))
                    a["mem"] += _mem_mib(r.get("memory"))

            usage: dict[str, tuple] = {}
            top = self.cluster_top("nodes", nodes=nodes)
            metrics_ok = bool(top.get("available"))
            if metrics_ok:
                for row in top["rows"]:
                    usage[row["name"]] = (row["cpu"], row["memory"])

            rows = []
            tot = {"cpu_alloc": 0, "mem_alloc": 0, "cpu_req": 0, "mem_req": 0,
                   "cpu_use": 0, "mem_use": 0, "pods": 0, "pod_cap": 0}
            for n in nodes:
                name = n.metadata.name
                alloc = n.status.allocatable or {}
                cpu_alloc = _cpu_milli(alloc.get("cpu"))
                mem_alloc = _mem_mib(alloc.get("memory"))
                pod_cap = int(alloc.get("pods", 0) or 0)
                r = req.get(name, {"cpu": 0, "mem": 0, "pods": 0})
                cpu_use, mem_use = usage.get(name, (None, None))
                rows.append({
                    "name": name,
                    "cpu_alloc": cpu_alloc, "cpu_req": r["cpu"], "cpu_use": cpu_use,
                    "cpu_req_pct": round(r["cpu"] / cpu_alloc * 100) if cpu_alloc else None,
                    "cpu_use_pct": round(cpu_use / cpu_alloc * 100) if (cpu_use and cpu_alloc) else None,
                    "mem_alloc": mem_alloc, "mem_req": r["mem"], "mem_use": mem_use,
                    "mem_req_pct": round(r["mem"] / mem_alloc * 100) if mem_alloc else None,
                    "mem_use_pct": round(mem_use / mem_alloc * 100) if (mem_use and mem_alloc) else None,
                    "pods": r["pods"], "pod_cap": pod_cap,
                    "schedulable": not (n.spec and n.spec.unschedulable),
                })
                tot["cpu_alloc"] += cpu_alloc
                tot["mem_alloc"] += mem_alloc
                tot["cpu_req"] += r["cpu"]
                tot["mem_req"] += r["mem"]
                tot["pods"] += r["pods"]
                tot["pod_cap"] += pod_cap
                tot["cpu_use"] += cpu_use or 0
                tot["mem_use"] += mem_use or 0
            rows.sort(key=lambda r: r["name"])
            tot["metrics_available"] = metrics_ok
            return {"nodes": rows, "totals": tot}

        return self._cached("capacity", 8.0, lambda: self._guard("capacity", _do))

    def rightsizing(self, namespace: str | None = None) -> dict:
        """Compare each pod's requests/limits to its live usage (metrics-server)
        and recommend right-sized requests. ``available`` is False when
        metrics-server is missing. Recommendation = ~1.2× current usage (with a
        small floor); flags over/under-provisioning and OOM risk."""

        def _do():
            top = self.cluster_top("pods", namespace=namespace)
            if not top.get("available"):
                return {"available": False, "message": top.get("message"), "rows": [], "total": 0}
            usage = {(r["namespace"], r["name"]): (r["cpu"], r["memory"]) for r in top["rows"]}

            pods = (
                self._core.list_namespaced_pod(namespace).items if namespace
                else self._core.list_pod_for_all_namespaces().items
            )
            rows = []
            for p in pods:
                ns, nm = p.metadata.namespace, p.metadata.name
                use = usage.get((ns, nm))
                if not use:
                    continue
                cpu_use, mem_use = use
                cpu_req = mem_req = cpu_lim = mem_lim = 0
                for c in (p.spec.containers or []):
                    r = (c.resources.requests or {}) if c.resources else {}
                    lim = (c.resources.limits or {}) if c.resources else {}
                    cpu_req += _cpu_milli(r.get("cpu"))
                    mem_req += _mem_mib(r.get("memory"))
                    cpu_lim += _cpu_milli(lim.get("cpu"))
                    mem_lim += _mem_mib(lim.get("memory"))
                verdict = []
                if not cpu_req:
                    verdict.append("sem request de CPU")
                elif cpu_req > cpu_use * 2 and cpu_req - cpu_use > 50:
                    verdict.append("CPU superdimensionada")
                elif cpu_use > cpu_req * 1.5:
                    verdict.append("CPU subdimensionada")
                if not mem_req:
                    verdict.append("sem request de memória")
                elif mem_req > mem_use * 2 and mem_req - mem_use > 64:
                    verdict.append("memória superdimensionada")
                elif mem_use > mem_req * 1.5:
                    verdict.append("memória subdimensionada")
                if mem_lim and mem_use > mem_lim * 0.9:
                    verdict.append("perto do limite de memória (risco de OOM)")
                rows.append({
                    "namespace": ns, "pod": nm,
                    "cpu_use": cpu_use, "cpu_req": cpu_req, "cpu_lim": cpu_lim,
                    "cpu_rec": max(10, round(cpu_use * 1.2)) if cpu_use else None,
                    "mem_use": mem_use, "mem_req": mem_req, "mem_lim": mem_lim,
                    "mem_rec": max(16, round(mem_use * 1.2)) if mem_use else None,
                    "verdict": verdict,
                })
            rows.sort(key=lambda r: (len(r["verdict"]) == 0, r["namespace"], r["pod"]))
            return {"available": True, "rows": rows, "total": len(rows)}

        key = f"rightsize:{namespace or '*'}"
        return self._cached(key, 8.0, lambda: self._guard("rightsizing", _do))

    # --- impact / blast radius (busca reversa de dependências) -----------

    def impact(self, kind: str, name: str, namespace: str | None = None) -> dict:
        """Reverse-dependency / blast-radius analysis for a resource.

        Answers "where is this used?" / "what breaks if this goes?" — questions
        the live API can't answer directly:

        * **configmaps / secrets / pvc**: which pods reference it and how
          (volume, projected volume, ``envFrom``, ``env``, ``imagePullSecret``),
          aggregated by owning workload.
        * **nodes**: which pods/workloads run on it, plus single-point-of-failure
          flags (workloads whose every replica sits on this node) and a drain
          impact summary.
        """
        kind = kind.lower()

        def _do():
            if kind == "nodes":
                pods = self._core.list_pod_for_all_namespaces(
                    field_selector=f"spec.nodeName={name}"
                ).items
                rs_ns = None
            elif kind in ("configmaps", "secrets", "pvc"):
                if not namespace:
                    raise KubernetesError(f"{kind} exige namespace para análise de impacto")
                pods = self._core.list_namespaced_pod(namespace).items
                rs_ns = namespace
            else:
                raise KubernetesError(f"análise de impacto não suportada para {kind}")

            # ReplicaSet -> owning Deployment (to collapse pods onto the Deployment).
            rss = (self._apps.list_namespaced_replica_set(rs_ns).items if rs_ns
                   else self._apps.list_replica_set_for_all_namespaces().items)
            rs_owner = {}
            for rs in rss:
                for o in (rs.metadata.owner_references or []):
                    if o.kind == "Deployment":
                        rs_owner[(rs.metadata.namespace, rs.metadata.name)] = o.name

            def workload_of(p):
                for o in (p.metadata.owner_references or []):
                    if o.kind == "ReplicaSet":
                        dep = rs_owner.get((p.metadata.namespace, o.name))
                        return ("Deployment", dep) if dep else ("ReplicaSet", o.name)
                    if o.kind in ("StatefulSet", "DaemonSet", "Job"):
                        return (o.kind, o.name)
                return None

            def refs_of(p):
                """How pod ``p`` references the target (empty list = no reference)."""
                spec = p.spec
                refs: set[str] = set()
                if kind == "pvc":
                    for v in (spec.volumes or []):
                        if v.persistent_volume_claim and v.persistent_volume_claim.claim_name == name:
                            refs.add("volume")
                    return sorted(refs)
                is_cm = kind == "configmaps"
                for v in (spec.volumes or []):
                    if is_cm and v.config_map and v.config_map.name == name:
                        refs.add("volume")
                    if not is_cm and v.secret and v.secret.secret_name == name:
                        refs.add("volume")
                    proj = v.projected
                    if proj:
                        for s in (proj.sources or []):
                            if is_cm and s.config_map and s.config_map.name == name:
                                refs.add("volume projetado")
                            if not is_cm and s.secret and s.secret.name == name:
                                refs.add("volume projetado")
                if not is_cm:
                    for ips in (spec.image_pull_secrets or []):
                        if ips.name == name:
                            refs.add("imagePullSecret")
                for c in (spec.containers or []) + (spec.init_containers or []):
                    for ef in (c.env_from or []):
                        if is_cm and ef.config_map_ref and ef.config_map_ref.name == name:
                            refs.add("envFrom")
                        if not is_cm and ef.secret_ref and ef.secret_ref.name == name:
                            refs.add("envFrom")
                    for e in (c.env or []):
                        vf = e.value_from
                        if not vf:
                            continue
                        if is_cm and vf.config_map_key_ref and vf.config_map_key_ref.name == name:
                            refs.add("env")
                        if not is_cm and vf.secret_key_ref and vf.secret_key_ref.name == name:
                            refs.add("env")
                return sorted(refs)

            consumers = []
            for p in pods:
                refs = ["agendado"] if kind == "nodes" else refs_of(p)
                if not refs:
                    continue
                wl = workload_of(p)
                consumers.append({
                    "pod": p.metadata.name, "namespace": p.metadata.namespace,
                    "node": p.spec.node_name,
                    "workload_kind": wl[0] if wl else None,
                    "workload": wl[1] if wl else None,
                    "phase": (p.status.phase if p.status else None),
                    "refs": refs,
                })

            # Aggregate consumers by owning workload (standalone pods stay as Pod).
            agg: dict[tuple, dict] = {}
            for c in consumers:
                wlk = c["workload_kind"] or "Pod"
                wln = c["workload"] or c["pod"]
                a = agg.setdefault((wlk, wln, c["namespace"]), {
                    "kind": wlk, "name": wln, "namespace": c["namespace"],
                    "pods": 0, "via": set(),
                })
                a["pods"] += 1
                a["via"].update(c["refs"])
            workloads = [
                {"kind": a["kind"], "name": a["name"], "namespace": a["namespace"],
                 "pods": a["pods"], "via": sorted(a["via"])}
                for a in agg.values()
            ]
            workloads.sort(key=lambda w: (w["kind"], w["namespace"] or "", w["name"]))

            result = {
                "target": {"kind": kind, "name": name, "namespace": namespace},
                "consumers": consumers,
                "workloads": workloads,
                "summary": {"pods": len(consumers), "workloads": len(workloads)},
            }

            if kind == "nodes":
                # SPOF: a Deployment/StatefulSet whose every desired replica is on
                # this node loses all of them if the node dies/drains.
                desired: dict[tuple, int] = {}
                for d in self._apps.list_deployment_for_all_namespaces().items:
                    desired[("Deployment", d.metadata.namespace, d.metadata.name)] = d.spec.replicas or 0
                for s in self._apps.list_stateful_set_for_all_namespaces().items:
                    desired[("StatefulSet", s.metadata.namespace, s.metadata.name)] = s.spec.replicas or 0
                spof = []
                for w in workloads:
                    rep = desired.get((w["kind"], w["namespace"], w["name"]))
                    if rep and rep > 0 and w["pods"] >= rep:
                        spof.append({**w, "replicas": rep})
                result["spof"] = spof
                result["summary"]["spof"] = len(spof)
            return result

        key = f"impact:{kind}:{namespace or ''}:{name}"
        return self._cached(key, 8.0, lambda: self._guard("impact analysis", _do))

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _pod_summary(pod) -> PodSummary:
        statuses = pod.status.container_statuses or []
        ready = sum(1 for c in statuses if c.ready)
        restarts = sum(c.restart_count for c in statuses)
        return PodSummary(
            name=pod.metadata.name,
            namespace=pod.metadata.namespace,
            phase=pod.status.phase,
            node=pod.spec.node_name,
            ready=f"{ready}/{len(statuses)}" if statuses else None,
            up=_short_age((pod.status.start_time if pod.status else None) or pod.metadata.creation_timestamp),
            restarts=restarts,
            containers=[c.name for c in (pod.spec.containers or [])],
        )

    @staticmethod
    def _deployment_summary(dep) -> DeploymentSummary:
        status = dep.status
        return DeploymentSummary(
            name=dep.metadata.name,
            namespace=dep.metadata.namespace,
            replicas=dep.spec.replicas or 0,
            ready_replicas=status.ready_replicas or 0,
            available_replicas=status.available_replicas or 0,
        )


# --- client cache --------------------------------------------------------
# Building a client re-parses the kubeconfig from disk and allocates a fresh
# urllib3 PoolManager; doing that per request also throws away HTTP keep-alive to
# the apiserver, paying a TCP+TLS handshake every time. We cache clients by
# (context, kubeconfig_path, insecure) and reuse them. The kubernetes ApiClient /
# urllib3 pool are safe to share across threads. Entries expire after a TTL so a
# rotated credential is eventually picked up; registry mutations also invalidate.
_CLIENT_TTL = 300.0  # seconds
_client_cache: dict[tuple, tuple[float, "KubernetesClient"]] = {}
_client_lock = threading.Lock()


def get_client(
    context: str, kubeconfig_path: str | None, insecure: bool = False
) -> "KubernetesClient":
    """Return a cached :class:`KubernetesClient`, building one on miss/expiry."""
    key = (context, kubeconfig_path, bool(insecure))
    now = time.monotonic()
    with _client_lock:
        hit = _client_cache.get(key)
        if hit is not None and now - hit[0] < _CLIENT_TTL:
            return hit[1]
    # Build outside the lock (disk/network). A concurrent miss may build twice;
    # harmless — the loser is GC'd and its pool closed.
    built = KubernetesClient(context, kubeconfig_path, insecure)
    with _client_lock:
        _client_cache[key] = (now, built)
        if len(_client_cache) > 64:  # opportunistic prune of stale entries
            for k, (ts, _) in list(_client_cache.items()):
                if now - ts >= _CLIENT_TTL:
                    _client_cache.pop(k, None)
    return built


def invalidate_client_cache() -> None:
    """Drop all cached clients (call after cluster registry mutations)."""
    with _client_lock:
        _client_cache.clear()
