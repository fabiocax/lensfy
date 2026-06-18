from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class ManifestVersion(Base, TimestampMixin):
    """A snapshot of a resource's YAML, saved each time it is applied.

    Gives the editor a rollback history per resource, keyed by
    (cluster_id, kind, namespace, name). Cluster-scoped kinds use ``""`` for
    namespace. Only the most recent few versions are retained per resource
    (see ``ManifestVersionService.MAX_VERSIONS``).
    """

    __tablename__ = "manifest_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(255), index=True)
    yaml: Mapped[str] = mapped_column(Text)

    @property
    def size(self) -> int:
        return len(self.yaml or "")
