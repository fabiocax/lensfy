from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class AIReport(Base, TimestampMixin):
    """A saved AI assistant report/conversation (diagnoses, analyses).

    The assistant chat is otherwise ephemeral; users save useful reports here to
    review later. ``content`` is the conversation transcript in Markdown.
    """

    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    cluster_name: Mapped[str | None] = mapped_column(String(255), default=None)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
