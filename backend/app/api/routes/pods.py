from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.schemas.workloads import PodSummary
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("", response_model=list[PodSummary])
def list_pods(
    cluster_id: int, service: WorkloadServiceDep, namespace: str | None = None
):
    try:
        return service.list_pods(cluster_id, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pod(
    cluster_id: int, name: str, namespace: str, service: WorkloadServiceDep
):
    try:
        service.delete_pod(cluster_id, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
