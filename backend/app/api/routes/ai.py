from fastapi import APIRouter, HTTPException, status

from app.ai.agent import status as ai_status
from app.api.deps import AIReportServiceDep
from app.schemas.ai_report import AIReportCreate, AIReportRead, AIReportSummary

router = APIRouter()


@router.get("/status")
def get_status():
    """Whether the AI assistant is configured (key present) + model/mutations."""
    return ai_status()


# --- saved reports (diagnoses/analyses) ---


@router.get("/reports", response_model=list[AIReportSummary])
def list_reports(service: AIReportServiceDep):
    return service.list()


@router.post("/reports", response_model=AIReportRead, status_code=status.HTTP_201_CREATED)
def create_report(payload: AIReportCreate, service: AIReportServiceDep):
    return service.create(payload)


@router.get("/reports/{report_id}", response_model=AIReportRead)
def get_report(report_id: int, service: AIReportServiceDep):
    report = service.get(report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Relatório não encontrado")
    return report


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: int, service: AIReportServiceDep):
    if not service.delete(report_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Relatório não encontrado")
