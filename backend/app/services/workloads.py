"""Read-through service for live cluster workloads (pods, deployments, metrics).

Resolves a cluster context from the local registry, then delegates to the
Kubernetes SDK wrapper. Kept separate from ClusterService since it never touches
the database for writes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.kubernetes.client import KubernetesClient, KubernetesError, get_client
from app.repositories.cluster import ClusterRepository
from app.schemas.workloads import ClusterMetrics, DeploymentSummary, PodSummary


class WorkloadServiceError(Exception):
    pass


class WorkloadService:
    def __init__(self, db: Session) -> None:
        self.repo = ClusterRepository(db)

    def _client(self, cluster_id: int) -> KubernetesClient:
        cluster = self.repo.get(cluster_id)
        if cluster is None:
            raise WorkloadServiceError(f"Cluster {cluster_id} not found")
        try:
            return get_client(
                cluster.context, cluster.kubeconfig_path, cluster.insecure
            )
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def list_pods(self, cluster_id: int, namespace: str | None) -> list[PodSummary]:
        try:
            return self._client(cluster_id).list_pods(namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def list_deployments(
        self, cluster_id: int, namespace: str | None
    ) -> list[DeploymentSummary]:
        try:
            return self._client(cluster_id).list_deployments(namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def scale_deployment(
        self, cluster_id: int, name: str, namespace: str, replicas: int
    ) -> DeploymentSummary:
        try:
            return self._client(cluster_id).scale_deployment(name, namespace, replicas)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def delete_pod(self, cluster_id: int, name: str, namespace: str) -> None:
        try:
            self._client(cluster_id).delete_pod(name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def metrics(self, cluster_id: int) -> ClusterMetrics:
        try:
            return self._client(cluster_id).cluster_metrics()
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def top(self, cluster_id: int, kind: str, namespace: str | None) -> dict:
        try:
            return self._client(cluster_id).cluster_top(kind, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def overview(self, cluster_id: int) -> dict:
        try:
            return self._client(cluster_id).cluster_overview()
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def list_resource(self, cluster_id: int, kind: str, namespace: str | None) -> dict:
        try:
            return self._client(cluster_id).list_resource(kind, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def get_manifest(
        self, cluster_id: int, kind: str, name: str, namespace: str | None
    ) -> str:
        try:
            return self._client(cluster_id).get_manifest(kind, name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def get_object(
        self, cluster_id: int, kind: str, name: str, namespace: str | None
    ) -> dict:
        try:
            return self._client(cluster_id).get_object(kind, name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def apply_manifest(
        self, cluster_id: int, kind: str, name: str, namespace: str | None, yaml_text: str
    ) -> str:
        try:
            return self._client(cluster_id).apply_manifest(
                kind, name, namespace, yaml_text
            )
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def deploy_manifests(
        self, cluster_id: int, yaml_text: str, namespace: str
    ) -> list[dict]:
        try:
            return self._client(cluster_id).deploy_manifests(yaml_text, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def validate_manifests(self, cluster_id, yaml_text, namespace):
        try:
            return self._client(cluster_id).validate_manifests(yaml_text, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def scale_workload(self, cluster_id, kind, name, namespace, replicas):
        try:
            self._client(cluster_id).scale_workload(kind, name, namespace, replicas)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def restart_workload(self, cluster_id, kind, name, namespace):
        try:
            self._client(cluster_id).restart_workload(kind, name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def delete_resource(self, cluster_id, kind, name, namespace):
        try:
            self._client(cluster_id).delete_resource(kind, name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def trigger_cronjob(self, cluster_id, name, namespace):
        try:
            return self._client(cluster_id).trigger_cronjob(name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def set_cronjob_suspend(self, cluster_id, name, namespace, suspend):
        try:
            self._client(cluster_id).set_cronjob_suspend(name, namespace, suspend)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def get_resource_data(self, cluster_id, kind, name, namespace):
        try:
            return self._client(cluster_id).get_resource_data(kind, name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    # --- node management ---
    def cordon_node(self, cluster_id, name, unschedulable):
        try:
            self._client(cluster_id).cordon_node(name, unschedulable)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def drain_node(self, cluster_id, name, grace_period=None):
        try:
            return self._client(cluster_id).drain_node(name, grace_period)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    # --- rollout management ---
    def rollout_pause(self, cluster_id, kind, name, namespace, paused):
        try:
            self._client(cluster_id).rollout_pause(kind, name, namespace, paused)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def rollout_history(self, cluster_id, kind, name, namespace):
        try:
            return self._client(cluster_id).rollout_history(kind, name, namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def rollout_undo(self, cluster_id, kind, name, namespace, revision):
        try:
            self._client(cluster_id).rollout_undo(kind, name, namespace, revision)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    # --- diagnostics ---
    def issues(self, cluster_id):
        try:
            return self._client(cluster_id).cluster_issues()
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    # --- resource management (requests/limits, data) ---
    def set_container_resources(self, cluster_id, kind, name, namespace, container, requests, limits):
        try:
            self._client(cluster_id).set_container_resources(
                kind, name, namespace, container, requests, limits
            )
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def namespace_budget(self, cluster_id, namespace=None):
        try:
            return self._client(cluster_id).namespace_budget(namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def update_resource_data(self, cluster_id, kind, name, namespace, data):
        try:
            self._client(cluster_id).update_resource_data(kind, name, namespace, data)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def topology(self, cluster_id, namespace=None):
        try:
            return self._client(cluster_id).traffic_graph(namespace)
        except KubernetesError as exc:
            raise WorkloadServiceError(str(exc)) from exc

    def start_port_forward(self, cluster_id, namespace, pod, remote_port, local_port):
        from app.kubernetes.portforward import manager

        core = self._client(cluster_id)._core
        try:
            return manager.start(cluster_id, core, namespace, pod, remote_port, local_port)
        except RuntimeError as exc:
            raise WorkloadServiceError(str(exc)) from exc
