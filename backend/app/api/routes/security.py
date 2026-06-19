"""Security posture and RBAC: pod security scan, "quem pode o quê" and a
``kubectl auth can-i`` simulator (SubjectAccessReview)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import WorkloadServiceDep
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("/scan")
def security_scan(
    cluster_id: int, service: WorkloadServiceDep, namespace: str | None = None
):
    """Scan workloads for risky security settings (privileged, hostPath,
    runAsRoot, dangerous capabilities, missing limits, mutable tags, …)."""
    try:
        return service.security_scan(cluster_id, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/rbac/subjects")
def rbac_subjects(cluster_id: int, service: WorkloadServiceDep):
    """Every RBAC subject and the verbs/resources granted by its bound roles."""
    try:
        return service.rbac_subjects(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class CanIRequest(BaseModel):
    verb: str = Field(..., examples=["get", "create", "delete", "*"])
    resource: str = Field(..., examples=["pods", "deployments", "secrets"])
    namespace: str | None = None
    group: str = ""
    subresource: str | None = None
    name: str | None = None
    # Optional subject; omit to check the current credential.
    user: str | None = None
    groups: list[str] | None = None
    serviceaccount: str | None = None


@router.post("/rbac/can-i")
def rbac_can_i(cluster_id: int, payload: CanIRequest, service: WorkloadServiceDep):
    """Authoritative permission check (SubjectAccessReview / SelfSubjectAccessReview)."""
    try:
        return service.rbac_can_i(cluster_id, **payload.model_dump())
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
