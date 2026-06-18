from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AIReportCreate(BaseModel):
    title: str
    content: str
    cluster_id: int | None = None
    cluster_name: str | None = None


class AIReportSummary(BaseModel):
    """List item — omits the full content to keep the listing light."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    cluster_id: int | None = None
    cluster_name: str | None = None
    created_at: datetime


class AIReportRead(AIReportSummary):
    content: str
