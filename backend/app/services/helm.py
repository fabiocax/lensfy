"""Helm release management on top of the helm CLI wrapper."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.kubernetes import helm
from app.repositories.cluster import ClusterRepository


class HelmServiceError(Exception):
    pass


class HelmService:
    def __init__(self, db: Session) -> None:
        self.repo = ClusterRepository(db)

    def _cluster(self, cluster_id: int):
        cluster = self.repo.get(cluster_id)
        if cluster is None:
            raise HelmServiceError(f"Cluster {cluster_id} não encontrado")
        return cluster

    def releases(self, cluster_id: int) -> dict:
        if not helm.available():
            return {
                "available": False,
                "message": "Helm CLI não encontrado no sistema (instale o `helm`).",
                "releases": [],
            }
        try:
            return {
                "available": True,
                "message": None,
                "releases": helm.list_releases(self._cluster(cluster_id)),
            }
        except helm.HelmError as exc:
            raise HelmServiceError(str(exc)) from exc

    def _do(self, fn, *args):
        try:
            return fn(*args)
        except helm.HelmError as exc:
            raise HelmServiceError(str(exc)) from exc

    def install(self, cluster_id, name, chart, namespace, version, repo):
        return self._do(helm.install, self._cluster(cluster_id), name, chart, namespace, version, repo)

    def upgrade(self, cluster_id, name, chart, namespace, version, repo):
        return self._do(helm.upgrade, self._cluster(cluster_id), name, chart, namespace, version, repo)

    def rollback(self, cluster_id, name, namespace, revision):
        return self._do(helm.rollback, self._cluster(cluster_id), name, namespace, revision)

    def uninstall(self, cluster_id, name, namespace):
        return self._do(helm.uninstall, self._cluster(cluster_id), name, namespace)
