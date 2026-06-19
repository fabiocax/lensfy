"""Cross-cluster operations: global search, cluster comparison and inventory.

Unlike :class:`WorkloadService` (single cluster), these fan out over every
cluster in the local registry. Each cluster is queried best-effort and in
parallel; an unreachable cluster is reported as an error entry instead of
failing the whole request.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from app.kubernetes.client import KubernetesError, get_client
from app.kubernetes.resources import RESOURCE_KINDS
from app.repositories.cluster import ClusterRepository

# pods/deployments have dedicated summary methods (not in the generic registry).
_SPECIAL_KINDS = ("pods", "deployments")
# Every kind name the cross-cluster features accept (registry + the specials).
_VALID_KINDS = set(RESOURCE_KINDS) | set(_SPECIAL_KINDS)
# Kinds searched by default for the global search (the ones worth name-matching).
_SEARCH_KINDS = (
    "pods", "deployments", "statefulsets", "daemonsets", "services",
    "ingress", "configmaps", "secrets", "pvc", "jobs", "cronjobs",
    "namespaces", "nodes",
)
_MAX_WORKERS = 8


def _rows_for(client, kind: str) -> list[dict]:
    """Return ``[{name, namespace}]`` for a kind, handling the pods/deployments
    summary methods and the generic registry uniformly."""
    if kind == "pods":
        return [{"name": p.name, "namespace": p.namespace} for p in client.list_pods(None)]
    if kind == "deployments":
        return [{"name": d.name, "namespace": d.namespace} for d in client.list_deployments(None)]
    return client.list_resource(kind, None).get("rows", [])


class MultiClusterServiceError(Exception):
    pass


class MultiClusterService:
    def __init__(self, db: Session) -> None:
        self.repo = ClusterRepository(db)

    def _clusters(self):
        return self.repo.list()

    def _client_for(self, cluster):
        return get_client(cluster.context, cluster.kubeconfig_path, cluster.insecure)

    # --- global search ----------------------------------------------------

    def search(
        self, query: str, kinds: list[str] | None = None,
        cluster_ids: list[int] | None = None, limit: int = 500,
    ) -> dict:
        """Find resources whose name contains ``query`` across clusters.

        Returns ``{query, results:[{cluster_id, cluster_name, kind, name,
        namespace}], errors:[{cluster_id, cluster_name, message}], total,
        truncated}``.
        """
        q = (query or "").strip().lower()
        if not q:
            raise MultiClusterServiceError("informe um termo de busca")
        wanted = [k for k in (kinds or _SEARCH_KINDS) if k in _VALID_KINDS]
        if not wanted:
            raise MultiClusterServiceError("nenhum tipo de recurso válido")

        clusters = [
            c for c in self._clusters()
            if not cluster_ids or c.id in set(cluster_ids)
        ]

        def scan(cluster):
            out = []
            try:
                client = self._client_for(cluster)
            except KubernetesError as exc:
                return [], {"cluster_id": cluster.id, "cluster_name": cluster.name,
                            "message": str(exc)}
            for kind in wanted:
                try:
                    rows = _rows_for(client, kind)
                except KubernetesError:
                    continue  # kind unsupported on this cluster — skip quietly
                for row in rows:
                    name = row.get("name") or ""
                    if q in name.lower():
                        out.append({
                            "cluster_id": cluster.id, "cluster_name": cluster.name,
                            "kind": kind, "name": name, "namespace": row.get("namespace"),
                        })
            return out, None

        results: list[dict] = []
        errors: list[dict] = []
        if clusters:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(clusters))) as pool:
                futures = {pool.submit(scan, c): c for c in clusters}
                for fut in as_completed(futures):
                    rows, err = fut.result()
                    results.extend(rows)
                    if err:
                        errors.append(err)

        results.sort(key=lambda r: (r["cluster_name"], r["kind"], r["name"]))
        truncated = len(results) > limit
        return {
            "query": query, "results": results[:limit], "errors": errors,
            "total": len(results), "truncated": truncated,
        }

    # --- compare ----------------------------------------------------------

    def compare(self, cluster_ids: list[int] | None = None) -> dict:
        """Side-by-side overview snapshot of several clusters (versions, node
        readiness, pod/deployment health, CPU/mem usage)."""
        clusters = [
            c for c in self._clusters()
            if not cluster_ids or c.id in set(cluster_ids)
        ]

        def snap(cluster):
            try:
                ov = self._client_for(cluster).cluster_overview()
            except KubernetesError as exc:
                return {"cluster_id": cluster.id, "cluster_name": cluster.name,
                        "reachable": False, "error": str(exc)}
            return {
                "cluster_id": cluster.id, "cluster_name": cluster.name,
                "reachable": True,
                "version": ov.get("version"),
                "counts": ov.get("counts", {}),
                "nodes": ov.get("nodes", {}),
                "pods": ov.get("pods", {}),
                "deployments": ov.get("deployments", {}),
                "usage": ov.get("usage", {}),
                "warnings_total": ov.get("warnings_total", 0),
            }

        rows = []
        if clusters:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(clusters))) as pool:
                futures = [pool.submit(snap, c) for c in clusters]
                rows = [f.result() for f in futures]
        rows.sort(key=lambda r: r["cluster_name"])
        return {"clusters": rows, "total": len(rows)}

    # --- inventory --------------------------------------------------------

    def inventory(self, cluster_id: int) -> dict:
        """Resource-count inventory for one cluster (per kind, plus per-namespace
        pod counts) — an exportable snapshot of what's deployed."""
        cluster = self.repo.get(cluster_id)
        if cluster is None:
            raise MultiClusterServiceError(f"Cluster {cluster_id} não encontrado")
        try:
            client = self._client_for(cluster)
        except KubernetesError as exc:
            raise MultiClusterServiceError(str(exc)) from exc

        kinds: dict[str, int] = {}
        per_namespace: dict[str, int] = {}
        for kind in (*_SPECIAL_KINDS, *RESOURCE_KINDS):
            if kind == "events":
                continue  # too noisy / unbounded to inventory
            try:
                rows = _rows_for(client, kind)
            except KubernetesError:
                continue
            kinds[kind] = len(rows)
            if kind == "pods":
                for row in rows:
                    ns = row.get("namespace") or "(sem namespace)"
                    per_namespace[ns] = per_namespace.get(ns, 0) + 1

        return {
            "cluster_id": cluster_id, "cluster_name": cluster.name,
            "context": cluster.context, "version": cluster.version,
            "kinds": dict(sorted(kinds.items())),
            "pods_per_namespace": dict(sorted(per_namespace.items())),
        }
