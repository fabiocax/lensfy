from sqlalchemy import select

from app.models.cluster import Cluster
from app.repositories.base import BaseRepository


class ClusterRepository(BaseRepository[Cluster]):
    model = Cluster

    def list(self) -> list[Cluster]:
        """Manual order first (drag-and-drop), id as a stable tiebreak."""
        return list(
            self.db.scalars(select(Cluster).order_by(Cluster.sort_order, Cluster.id))
        )

    def max_sort_order(self) -> int:
        from sqlalchemy import func

        return self.db.scalar(select(func.max(Cluster.sort_order))) or 0

    def get_by_context(self, context: str) -> Cluster | None:
        return self.db.scalar(select(Cluster).where(Cluster.context == context))

    def get_by_context_path(self, context: str, path: str | None) -> Cluster | None:
        """Match a cluster by context AND kubeconfig source (same context name can
        come from different kubeconfigs)."""
        return self.db.scalar(
            select(Cluster).where(
                Cluster.context == context, Cluster.kubeconfig_path == path
            )
        )
