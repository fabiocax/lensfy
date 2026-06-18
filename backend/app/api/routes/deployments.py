from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.schemas.workloads import DeploymentSummary, ScaleRequest
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("", response_model=list[DeploymentSummary])
def list_deployments(
    cluster_id: int, service: WorkloadServiceDep, namespace: str | None = None
):
    try:
        return service.list_deployments(cluster_id, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.patch("/{name}/scale", response_model=DeploymentSummary)
def scale_deployment(
    cluster_id: int,
    name: str,
    namespace: str,
    payload: ScaleRequest,
    service: WorkloadServiceDep,
):
    try:
        return service.scale_deployment(cluster_id, name, namespace, payload.replicas)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
