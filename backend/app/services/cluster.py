"""Business logic for cluster management: import kubeconfig, detect contexts,
refresh status/version, and CRUD over the local cluster registry.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.kubernetes.client import (
    ContextInfo,
    KubernetesClient,
    KubernetesError,
    contexts_from_kubeconfig,
    invalidate_client_cache,
)
from app.models.cluster import Cluster
from app.repositories.cluster import ClusterRepository
from app.schemas.cluster import ClusterCreate, KubeconfigSource, ClusterUpdate

logger = get_logger(__name__)


class ClusterServiceError(Exception):
    """Domain error surfaced as a 4xx by the API layer."""


# Distinct accent colors auto-assigned to new clusters (cycled).
_PALETTE = [
    "#3498db", "#2ecc71", "#9b59b6", "#e67e22",
    "#e74c3c", "#1abc9c", "#e84393", "#f39c12",
]


class ClusterService:
    def __init__(self, db: Session) -> None:
        self.repo = ClusterRepository(db)

    def list(self) -> list[Cluster]:
        return self.repo.list()

    def get(self, cluster_id: int) -> Cluster:
        cluster = self.repo.get(cluster_id)
        if cluster is None:
            raise ClusterServiceError(f"Cluster {cluster_id} not found")
        return cluster

    def detect_contexts(self, source: KubeconfigSource) -> list[ContextInfo]:
        """List the contexts in a kubeconfig (path or pasted content) without importing."""
        try:
            return contexts_from_kubeconfig(
                source.kubeconfig_path, source.kubeconfig_content
            )
        except KubernetesError as exc:
            raise ClusterServiceError(str(exc)) from exc

    def import_from_kubeconfig(self, payload: ClusterCreate) -> list[Cluster]:
        """Create cluster records for the selected contexts in a kubeconfig.

        Provide a ``kubeconfig_path`` or ``kubeconfig_content`` (persisted to a
        managed file so contexts stay loadable). ``contexts`` (or legacy
        ``context``) selects which to import; empty selects all.
        """
        path = self._resolve_kubeconfig_path(payload)

        try:
            contexts = contexts_from_kubeconfig(path)
        except KubernetesError as exc:
            raise ClusterServiceError(str(exc)) from exc

        wanted = set(payload.contexts) | ({payload.context} if payload.context else set())
        if wanted:
            contexts = [c for c in contexts if c.name in wanted]
            if not contexts:
                raise ClusterServiceError("Nenhum dos contextos selecionados foi encontrado")

        color_n = len(self.repo.list())  # cycle the palette across all clusters
        order_n = self.repo.max_sort_order()  # new clusters append to the end
        imported: list[Cluster] = []
        for ctx in contexts:
            # Only the same context FROM THE SAME kubeconfig is an update; a context
            # with the same name from a different file is a distinct cluster.
            existing = self.repo.get_by_context_path(ctx.name, path)
            if existing:
                existing.insecure = payload.insecure
                imported.append(self.repo.add(existing))
                continue
            order_n += 1
            cluster = Cluster(
                name=ctx.name,
                context=ctx.name,
                provider=self._guess_provider(ctx.cluster),
                kubeconfig_path=path,
                insecure=payload.insecure,
                color=_PALETTE[color_n % len(_PALETTE)],
                status="unknown",
                sort_order=order_n,
            )
            color_n += 1
            imported.append(self.repo.add(cluster))

        if not imported:
            raise ClusterServiceError("Nenhum contexto novo para importar")

        # A re-import can overwrite a managed kubeconfig at the same path, which
        # would otherwise be masked by a cached client until the TTL expires.
        invalidate_client_cache()
        # Don't refresh synchronously: probing an unreachable cluster could hang
        # the response. Imported clusters start "unknown"; the UI refreshes them
        # in the background (or on selection).
        return imported

    # --- gcloud / GKE import ---------------------------------------------

    def gcloud_status(self) -> dict:
        from app.kubernetes import gcloud

        return gcloud.status()

    def gcloud_projects(self) -> list[dict]:
        from app.kubernetes import gcloud

        try:
            return gcloud.list_projects()
        except gcloud.GcloudError as exc:
            raise ClusterServiceError(str(exc)) from exc

    def gcloud_clusters(self, project: str) -> list[dict]:
        from app.kubernetes import gcloud

        try:
            return gcloud.list_clusters(project)
        except gcloud.GcloudError as exc:
            raise ClusterServiceError(str(exc)) from exc

    def import_from_gcloud(self, refs: list, insecure: bool = False) -> list[Cluster]:
        """Write GKE credentials into a managed kubeconfig and import the contexts.

        ``refs`` is a list of objects with ``name``/``location``/``project``.
        Reuses :meth:`import_from_kubeconfig` so dedup, palette and background
        refresh all apply; created clusters are renamed to the GKE short name.
        """
        from app.kubernetes import gcloud

        if not refs:
            raise ClusterServiceError("Nenhum cluster selecionado")

        kube_dir = get_settings().data_dir / "kubeconfigs"
        kube_dir.mkdir(parents=True, exist_ok=True)
        path = str(kube_dir / "gcloud.yaml")

        ctx_to_name: dict[str, str] = {}
        try:
            for ref in refs:
                ctx = gcloud.get_credentials(ref.name, ref.location, ref.project, path)
                ctx_to_name[ctx] = ref.name
        except gcloud.GcloudError as exc:
            raise ClusterServiceError(str(exc)) from exc

        payload = ClusterCreate(
            kubeconfig_path=path, contexts=list(ctx_to_name), insecure=insecure
        )
        clusters = self.import_from_kubeconfig(payload)
        # Friendly name = GKE short name (the context is the long gke_… string).
        for cluster in clusters:
            friendly = ctx_to_name.get(cluster.context)
            if friendly and cluster.name == cluster.context:
                cluster.name = friendly
                cluster.provider = "gcp"
                self.repo.add(cluster)
        return clusters

    def update(self, cluster_id: int, payload: ClusterUpdate) -> Cluster:
        cluster = self.get(cluster_id)
        if payload.name is not None:
            cluster.name = payload.name
        if payload.favorite is not None:
            cluster.favorite = payload.favorite
        if payload.insecure is not None:
            cluster.insecure = payload.insecure
        if payload.color is not None:
            cluster.color = payload.color
        saved = self.repo.add(cluster)
        invalidate_client_cache()  # creds/insecure may have changed
        return saved

    def delete(self, cluster_id: int) -> None:
        self.repo.delete(self.get(cluster_id))
        invalidate_client_cache()  # free the cached client's connection pool

    def reorder(self, ordered_ids: list[int]) -> list[Cluster]:
        """Apply a manual ordering: ``sort_order`` follows the given id sequence.

        Ids not present keep their relative order after the listed ones.
        """
        position = {cid: i for i, cid in enumerate(ordered_ids)}
        tail = len(ordered_ids)
        for cluster in self.repo.list():
            cluster.sort_order = position.get(cluster.id, tail + cluster.id)
            self.db.add(cluster)
        self.db.commit()
        return self.repo.list()

    @property
    def db(self):
        return self.repo.db

    def refresh(self, cluster_id: int) -> Cluster:
        """Probe the live cluster to update status and server version."""
        cluster = self.get(cluster_id)
        try:
            k8s = KubernetesClient(
                cluster.context, cluster.kubeconfig_path, cluster.insecure
            )
            version = k8s.server_version()
            cluster.version = version
            cluster.status = "connected" if version else "unreachable"
        except KubernetesError as exc:
            logger.warning("refresh failed for %s: %s", cluster.context, exc)
            cluster.status = "unreachable"
        return self.repo.add(cluster)

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _resolve_kubeconfig_path(payload: ClusterCreate) -> str | None:
        import os
        from uuid import uuid4

        if payload.kubeconfig_path:
            return os.path.expanduser(payload.kubeconfig_path)
        if payload.kubeconfig_content:
            # Persist pasted/uploaded content to a managed file so the client can
            # keep loading the context by path after import.
            kube_dir = get_settings().data_dir / "kubeconfigs"
            kube_dir.mkdir(parents=True, exist_ok=True)
            target = kube_dir / f"{uuid4().hex}.yaml"
            target.write_text(payload.kubeconfig_content, encoding="utf-8")
            return str(target)
        return None  # falls back to the default ~/.kube/config

    @staticmethod
    def _guess_provider(cluster_name: str | None) -> str | None:
        if not cluster_name:
            return None
        lowered = cluster_name.lower()
        markers = {
            "eks": "aws",
            "gke": "gcp",
            "aks": "azure",
            "minikube": "minikube",
            "kind": "kind",
            "k3s": "k3s",
            "docker-desktop": "docker-desktop",
        }
        for marker, provider in markers.items():
            if marker in lowered:
                return provider
        return None
