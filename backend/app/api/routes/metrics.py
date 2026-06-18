from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.schemas.workloads import ClusterMetrics, TopResponse
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("", response_model=ClusterMetrics)
def cluster_metrics(cluster_id: int, service: WorkloadServiceDep):
    try:
        return service.metrics(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/overview")
def overview(cluster_id: int, service: WorkloadServiceDep):
    """Rich dashboard snapshot (counts + health + recent warnings + usage)."""
    try:
        return service.overview(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/topology")
def topology(cluster_id: int, service: WorkloadServiceDep, namespace: str | None = None):
    """Traffic topology graph (Ingress → Service → Workload → Pods) for the map."""
    try:
        return service.topology(cluster_id, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/budget")
def budget(cluster_id: int, service: WorkloadServiceDep, namespace: str | None = None):
    """Per-namespace requests/limits budget + pods missing requests/limits (SLA risk)."""
    try:
        return service.namespace_budget(cluster_id, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/issues")
def issues(cluster_id: int, service: WorkloadServiceDep):
    """Cluster problems for the 'Problemas' view: not-ready/crashlooping pods,
    unhealthy workloads, failed jobs, unbound PVCs, unhealthy/cordoned nodes."""
    try:
        return service.issues(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/top", response_model=TopResponse)
def top(
    cluster_id: int, service: WorkloadServiceDep, kind: str = "nodes",
    namespace: str | None = None,
):
    """Live CPU/memory usage (metrics-server). available=false if not installed."""
    if kind not in ("nodes", "pods"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "kind deve ser nodes ou pods")
    try:
        return service.top(cluster_id, kind, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
