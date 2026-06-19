"""Cross-cluster features: global search, comparison and inventory."""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import MultiClusterServiceDep
from app.services.multicluster import MultiClusterServiceError

router = APIRouter()


@router.get("/search")
def search(
    q: str,
    service: MultiClusterServiceDep,
    kinds: list[str] | None = Query(default=None),
    cluster_ids: list[int] | None = Query(default=None),
    limit: int = 500,
):
    """Find resources by name substring across every registered cluster."""
    try:
        return service.search(q, kinds, cluster_ids, limit)
    except MultiClusterServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/compare")
def compare(
    service: MultiClusterServiceDep,
    cluster_ids: list[int] | None = Query(default=None),
):
    """Side-by-side overview snapshot of several clusters."""
    return service.compare(cluster_ids)


@router.get("/inventory")
def inventory(cluster_id: int, service: MultiClusterServiceDep):
    """Resource-count inventory for one cluster (exportable snapshot)."""
    try:
        return service.inventory(cluster_id)
    except MultiClusterServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
