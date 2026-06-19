"""Registry of Kubernetes resource kinds for the Explorer tree.

Each kind maps to: whether it is namespaced, the table columns to show, a
``list_fn(client, namespace)`` returning SDK objects, and a ``row_fn(obj)``
extracting a flat dict keyed by column. Keeping this declarative means new
read-only resources are a few lines, not a new endpoint.

The ``client`` passed to ``list_fn`` is a ``KubernetesClient`` (we reach its
private ``_core``/``_apps``/``_batch``/``_net``/``_storage`` API handles).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class ResourceDef:
    namespaced: bool
    columns: list[tuple[str, str]]  # (key, label)
    list_fn: Callable
    row_fn: Callable


# ---------- formatting helpers ----------

def age(ts: datetime | None) -> str:
    if not ts:
        return "-"
    secs = int((datetime.now(timezone.utc) - ts).total_seconds())
    secs = max(secs, 0)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d"
    if hours:
        return f"{hours}h"
    if mins:
        return f"{mins}m"
    return f"{secs}s"


def _node_ready(node) -> str:
    ready = "Unknown"
    for cond in node.status.conditions or []:
        if cond.type == "Ready":
            ready = "Ready" if cond.status == "True" else "NotReady"
            break
    # Mirror kubectl: a cordoned node reads "Ready,SchedulingDisabled".
    spec = getattr(node, "spec", None)
    if spec and getattr(spec, "unschedulable", None):
        ready += ",SchedulingDisabled"
    return ready


def _node_roles(node) -> str:
    prefix = "node-role.kubernetes.io/"
    roles = [
        k[len(prefix):] or "master"
        for k in (node.metadata.labels or {})
        if k.startswith(prefix)
    ]
    return ", ".join(sorted(roles)) or "<none>"


def _svc_ports(svc) -> str:
    parts = []
    for p in svc.spec.ports or []:
        s = f"{p.port}"
        if p.node_port:
            s += f":{p.node_port}"
        s += f"/{p.protocol}"
        parts.append(s)
    return ", ".join(parts) or "-"


def _ingress_hosts(ing) -> str:
    hosts = [r.host for r in (ing.spec.rules or []) if r.host]
    return ", ".join(hosts) or "*"


def _mem_gib(mem: str | None) -> str:
    if not mem:
        return "-"
    units = {"Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1.0, "Ti": 1024.0}
    for suffix, factor in units.items():
        if mem.endswith(suffix):
            try:
                return f"{float(mem[:-2]) * factor:.1f}Gi"
            except ValueError:
                return mem
    return mem


def _job_status(job) -> str:
    s = job.status
    if s.succeeded:
        return "Complete"
    if s.failed:
        return "Failed"
    if s.active:
        return "Active"
    return "Pending"


def _quota_summary(q) -> str:
    """Compact ResourceQuota usage: ``cpu 2/4 · pods 3/10`` (first few entries)."""
    st = q.status
    hard = dict((st.hard or {})) if st else {}
    used = dict((st.used or {})) if st else {}
    if not hard:
        return "-"
    parts = [f"{k.split('/')[-1]} {used.get(k, '0')}/{v}" for k, v in sorted(hard.items())]
    return " · ".join(parts[:4]) + (" …" if len(parts) > 4 else "")


RESOURCES: dict[str, ResourceDef] = {
    # ---- Cluster-scoped ----
    "nodes": ResourceDef(
        namespaced=False,
        columns=[("name", "Nome"), ("status", "Status"), ("roles", "Roles"),
                 ("cpu", "CPU"), ("memory", "Memória"), ("os", "SO"),
                 ("version", "Versão"), ("age", "Idade")],
        list_fn=lambda c, ns: c._core.list_node().items,
        row_fn=lambda o: {
            "name": o.metadata.name,
            "status": _node_ready(o),
            "roles": _node_roles(o),
            "cpu": (o.status.capacity or {}).get("cpu", "-"),
            "memory": _mem_gib((o.status.capacity or {}).get("memory")),
            "os": o.status.node_info.os_image,
            "version": o.status.node_info.kubelet_version,
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "namespaces": ResourceDef(
        namespaced=False,
        columns=[("name", "Nome"), ("status", "Status"), ("age", "Idade")],
        list_fn=lambda c, ns: c._core.list_namespace().items,
        row_fn=lambda o: {
            "name": o.metadata.name,
            "status": o.status.phase,
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "storageclasses": ResourceDef(
        namespaced=False,
        columns=[("name", "Nome"), ("provisioner", "Provisioner"),
                 ("reclaim", "Reclaim"), ("default", "Padrão"), ("age", "Idade")],
        list_fn=lambda c, ns: c._storage.list_storage_class().items,
        row_fn=lambda o: {
            "name": o.metadata.name,
            "provisioner": o.provisioner,
            "reclaim": o.reclaim_policy,
            "default": "sim" if (o.metadata.annotations or {}).get(
                "storageclass.kubernetes.io/is-default-class") == "true" else "",
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Workloads ----
    "statefulsets": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("ready", "Ready"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._apps.list_namespaced_stateful_set(ns).items if ns
            else c._apps.list_stateful_set_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "ready": f"{o.status.ready_replicas or 0}/{o.spec.replicas or 0}",
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "daemonsets": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("desired", "Desejado"),
                 ("ready", "Ready"), ("available", "Disponível"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._apps.list_namespaced_daemon_set(ns).items if ns
            else c._apps.list_daemon_set_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "desired": o.status.desired_number_scheduled,
            "ready": o.status.number_ready,
            "available": o.status.number_available or 0,
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "jobs": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("completions", "Completions"), ("status", "Status"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._batch.list_namespaced_job(ns).items if ns
            else c._batch.list_job_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "completions": f"{o.status.succeeded or 0}/{o.spec.completions or 1}",
            "status": _job_status(o),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "cronjobs": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("schedule", "Schedule"),
                 ("suspend", "Suspenso"), ("active", "Ativos"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._batch.list_namespaced_cron_job(ns).items if ns
            else c._batch.list_cron_job_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "schedule": o.spec.schedule,
            "suspend": "sim" if o.spec.suspend else "",
            "active": len(o.status.active or []),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Network ----
    "services": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("type", "Tipo"),
                 ("cluster_ip", "Cluster IP"), ("ports", "Portas"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_service(ns).items if ns
            else c._core.list_service_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "type": o.spec.type,
            "cluster_ip": o.spec.cluster_ip,
            "ports": _svc_ports(o),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "ingress": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("hosts", "Hosts"),
                 ("class", "Class"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._net.list_namespaced_ingress(ns).items if ns
            else c._net.list_ingress_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "hosts": _ingress_hosts(o),
            "class": o.spec.ingress_class_name or "-",
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "networkpolicies": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("pod_selector", "Pod Selector"), ("types", "Tipos"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._net.list_namespaced_network_policy(ns).items if ns
            else c._net.list_network_policy_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "pod_selector": ", ".join(
                f"{k}={v}" for k, v in ((o.spec.pod_selector.match_labels or {})
                                        if o.spec and o.spec.pod_selector else {}).items()
            ) or "<todos>",
            "types": ", ".join(o.spec.policy_types or []) if o.spec else "-",
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Config ----
    "configmaps": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("keys", "Chaves"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_config_map(ns).items if ns
            else c._core.list_config_map_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "keys": len(o.data or {}),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "secrets": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("type", "Tipo"),
                 ("keys", "Chaves"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_secret(ns).items if ns
            else c._core.list_secret_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "type": o.type,
            "keys": len(o.data or {}),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Storage ----
    "pvc": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("status", "Status"),
                 ("capacity", "Capacidade"), ("storage_class", "Storage Class"),
                 ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_persistent_volume_claim(ns).items if ns
            else c._core.list_persistent_volume_claim_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "status": o.status.phase,
            "capacity": (o.status.capacity or {}).get("storage", "-"),
            "storage_class": o.spec.storage_class_name or "-",
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Resource governance ----
    "limitranges": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("types", "Tipos"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_limit_range(ns).items if ns
            else c._core.list_limit_range_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "types": ", ".join(sorted({i.type for i in (o.spec.limits or [])})) or "-",
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "resourcequotas": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("usage", "Uso (used/hard)"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_resource_quota(ns).items if ns
            else c._core.list_resource_quota_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "usage": _quota_summary(o),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Access control (RBAC) ----
    "roles": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"),
                 ("rules", "Regras"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._rbac.list_namespaced_role(ns).items if ns
            else c._rbac.list_role_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "rules": len(o.rules or []),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "clusterroles": ResourceDef(
        namespaced=False,
        columns=[("name", "Nome"), ("rules", "Regras"), ("age", "Idade")],
        list_fn=lambda c, ns: c._rbac.list_cluster_role().items,
        row_fn=lambda o: {
            "name": o.metadata.name,
            "rules": len(o.rules or []),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "rolebindings": ResourceDef(
        namespaced=True,
        columns=[("name", "Nome"), ("namespace", "Namespace"), ("role", "Role"),
                 ("subjects", "Subjects"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._rbac.list_namespaced_role_binding(ns).items if ns
            else c._rbac.list_role_binding_for_all_namespaces().items),
        row_fn=lambda o: {
            "name": o.metadata.name,
            "namespace": o.metadata.namespace,
            "role": f"{o.role_ref.kind}/{o.role_ref.name}",
            "subjects": len(o.subjects or []),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    "clusterrolebindings": ResourceDef(
        namespaced=False,
        columns=[("name", "Nome"), ("role", "Role"),
                 ("subjects", "Subjects"), ("age", "Idade")],
        list_fn=lambda c, ns: c._rbac.list_cluster_role_binding().items,
        row_fn=lambda o: {
            "name": o.metadata.name,
            "role": f"{o.role_ref.kind}/{o.role_ref.name}",
            "subjects": len(o.subjects or []),
            "age": age(o.metadata.creation_timestamp),
        },
    ),
    # ---- Events ----
    "events": ResourceDef(
        namespaced=True,
        columns=[("type", "Tipo"), ("reason", "Motivo"), ("object", "Objeto"),
                 ("message", "Mensagem"), ("age", "Idade")],
        list_fn=lambda c, ns: (
            c._core.list_namespaced_event(ns).items if ns
            else c._core.list_event_for_all_namespaces().items),
        row_fn=lambda o: {
            "namespace": o.metadata.namespace,
            "type": o.type,
            "reason": o.reason,
            "object": f"{o.involved_object.kind}/{o.involved_object.name}",
            "message": o.message,
            "age": age(o.last_timestamp or o.event_time or o.metadata.creation_timestamp),
        },
    ),
}

RESOURCE_KINDS = tuple(RESOURCES.keys())


# Kinds the YAML viewer can fetch/apply via the dynamic client.
# UI key -> (apiVersion, Kubernetes Kind, namespaced). Includes pods and
# deployments (which have their own table endpoints) plus every RESOURCES kind
# except events (which aren't usefully edited).
MANIFEST_KINDS: dict[str, tuple[str, str, bool]] = {
    "pods": ("v1", "Pod", True),
    "deployments": ("apps/v1", "Deployment", True),
    "statefulsets": ("apps/v1", "StatefulSet", True),
    "daemonsets": ("apps/v1", "DaemonSet", True),
    "jobs": ("batch/v1", "Job", True),
    "cronjobs": ("batch/v1", "CronJob", True),
    "services": ("v1", "Service", True),
    "ingress": ("networking.k8s.io/v1", "Ingress", True),
    "networkpolicies": ("networking.k8s.io/v1", "NetworkPolicy", True),
    "configmaps": ("v1", "ConfigMap", True),
    "secrets": ("v1", "Secret", True),
    "pvc": ("v1", "PersistentVolumeClaim", True),
    "limitranges": ("v1", "LimitRange", True),
    "resourcequotas": ("v1", "ResourceQuota", True),
    "nodes": ("v1", "Node", False),
    "namespaces": ("v1", "Namespace", False),
    "storageclasses": ("storage.k8s.io/v1", "StorageClass", False),
    "roles": ("rbac.authorization.k8s.io/v1", "Role", True),
    "clusterroles": ("rbac.authorization.k8s.io/v1", "ClusterRole", False),
    "rolebindings": ("rbac.authorization.k8s.io/v1", "RoleBinding", True),
    "clusterrolebindings": ("rbac.authorization.k8s.io/v1", "ClusterRoleBinding", False),
}
