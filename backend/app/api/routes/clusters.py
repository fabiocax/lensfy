from fastapi import APIRouter, HTTPException, status

from app.api.deps import ClusterServiceDep
from app.schemas.cluster import (
    ClusterCreate,
    ClusterRead,
    ClusterReorder,
    ClusterUpdate,
    GcloudImport,
    KubeContext,
    KubeconfigSource,
)
from app.services.cluster import ClusterServiceError

router = APIRouter()


@router.get("", response_model=list[ClusterRead])
def list_clusters(service: ClusterServiceDep):
    return service.list()


@router.get("/gcloud/status")
def gcloud_status(service: ClusterServiceDep):
    """Whether gcloud (and the GKE auth plugin) are installed."""
    return service.gcloud_status()


@router.get("/gcloud/projects")
def gcloud_projects(service: ClusterServiceDep):
    try:
        return service.gcloud_projects()
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/gcloud/clusters")
def gcloud_clusters(project: str, service: ClusterServiceDep):
    try:
        return service.gcloud_clusters(project)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/gcloud", response_model=list[ClusterRead], status_code=status.HTTP_201_CREATED)
def import_gcloud(payload: GcloudImport, service: ClusterServiceDep):
    try:
        return service.import_from_gcloud(payload.clusters, payload.insecure)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/contexts", response_model=list[KubeContext])
def detect_contexts(source: KubeconfigSource, service: ClusterServiceDep):
    """List the contexts available in a kubeconfig (path or pasted content)."""
    try:
        return service.detect_contexts(source)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/reorder", response_model=list[ClusterRead])
def reorder_clusters(payload: ClusterReorder, service: ClusterServiceDep):
    """Persist a manual drag-and-drop ordering of the cluster list."""
    return service.reorder(payload.order)


@router.post("", response_model=list[ClusterRead], status_code=status.HTTP_201_CREATED)
def import_clusters(payload: ClusterCreate, service: ClusterServiceDep):
    try:
        return service.import_from_kubeconfig(payload)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{cluster_id}", response_model=ClusterRead)
def get_cluster(cluster_id: int, service: ClusterServiceDep):
    try:
        return service.get(cluster_id)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.patch("/{cluster_id}", response_model=ClusterRead)
def update_cluster(cluster_id: int, payload: ClusterUpdate, service: ClusterServiceDep):
    try:
        return service.update(cluster_id, payload)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/{cluster_id}/refresh", response_model=ClusterRead)
def refresh_cluster(cluster_id: int, service: ClusterServiceDep):
    try:
        return service.refresh(cluster_id)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(cluster_id: int, service: ClusterServiceDep):
    try:
        service.delete(cluster_id)
    except ClusterServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
