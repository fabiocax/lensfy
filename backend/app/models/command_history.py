from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class CommandHistory(Base, TimestampMixin):
    """Local audit trail of commands/actions executed against a cluster.

    Supports the "command history" and "local audit" differentiators in the spec.
    """

    __tablename__ = "command_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    context: Mapped[str | None] = mapped_column(String(255), default=None)
    kind: Mapped[str] = mapped_column(String(64))  # e.g. exec, scale, delete, apply
    target: Mapped[str | None] = mapped_column(String(512), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
