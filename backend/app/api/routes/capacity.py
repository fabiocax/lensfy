"""Capacity planning and rightsizing.

``/capacity`` exposes per-node allocatable vs requested vs live usage (scheduling
headroom). ``/rightsizing`` compares each pod's requests/limits to live usage and
recommends adjustments (needs metrics-server).
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("")
def capacity(cluster_id: int, service: WorkloadServiceDep):
    """Per-node allocatable vs requested vs used, with cluster totals."""
    try:
        return service.capacity(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/rightsizing")
def rightsizing(
    cluster_id: int, service: WorkloadServiceDep, namespace: str | None = None
):
    """Right-sizing recommendations from live usage vs requests/limits."""
    try:
        return service.rightsizing(cluster_id, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
