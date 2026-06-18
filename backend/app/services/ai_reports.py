"""Persistence for saved AI assistant reports (diagnoses/analyses)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_report import AIReport
from app.schemas.ai_report import AIReportCreate


class AIReportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list(self) -> list[AIReport]:
        return list(
            self.db.scalars(select(AIReport).order_by(AIReport.created_at.desc()))
        )

    def get(self, report_id: int) -> AIReport | None:
        return self.db.get(AIReport, report_id)

    def create(self, payload: AIReportCreate) -> AIReport:
        report = AIReport(
            title=(payload.title or "Relatório").strip()[:255],
            content=payload.content,
            cluster_id=payload.cluster_id,
            cluster_name=payload.cluster_name,
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def delete(self, report_id: int) -> bool:
        report = self.get(report_id)
        if report is None:
            return False
        self.db.delete(report)
        self.db.commit()
        return True
