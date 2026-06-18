from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class Cluster(Base, TimestampMixin):
    """A Kubernetes cluster known to Lensfy, derived from a kubeconfig context."""

    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # Not unique: different kubeconfigs commonly share a context name
    # (e.g. the kubeadm default "kubernetes-admin@kubernetes").
    context: Mapped[str] = mapped_column(String(255), index=True)
    server: Mapped[str | None] = mapped_column(String(512), default=None)
    provider: Mapped[str | None] = mapped_column(String(128), default=None)
    version: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    # Skip TLS verification for this cluster (self-signed / rotated server cert).
    insecure: Mapped[bool] = mapped_column(Boolean, default=False)
    # Accent color (hex) for quick visual identification in the UI.
    color: Mapped[str | None] = mapped_column(String(7), default=None)
    # Manual ordering in the cluster list (drag-and-drop); ties break by id.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Path to the kubeconfig file this context was imported from.
    kubeconfig_path: Mapped[str | None] = mapped_column(Text, default=None)
