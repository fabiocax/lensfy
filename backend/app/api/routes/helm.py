"""Helm release management routes."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import HelmServiceDep
from app.schemas.workloads import HelmInstall, HelmReleases, HelmRollback
from app.services.helm import HelmServiceError

router = APIRouter()


@router.get("/releases", response_model=HelmReleases)
def list_releases(cluster_id: int, service: HelmServiceDep):
    """List Helm releases. available=false when the helm binary is missing."""
    try:
        return service.releases(cluster_id)
    except HelmServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/install")
def install(cluster_id: int, payload: HelmInstall, service: HelmServiceDep):
    try:
        out = service.install(
            cluster_id, payload.name, payload.chart, payload.namespace,
            payload.version, payload.repo,
        )
    except HelmServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"status": "installed", "output": out}


@router.post("/upgrade")
def upgrade(cluster_id: int, payload: HelmInstall, service: HelmServiceDep):
    try:
        out = service.upgrade(
            cluster_id, payload.name, payload.chart, payload.namespace,
            payload.version, payload.repo,
        )
    except HelmServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"status": "upgraded", "output": out}


@router.post("/rollback")
def rollback(cluster_id: int, payload: HelmRollback, service: HelmServiceDep):
    try:
        out = service.rollback(cluster_id, payload.name, payload.namespace, payload.revision)
    except HelmServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"status": "rolled_back", "output": out}


@router.delete("")
def uninstall(cluster_id: int, name: str, namespace: str, service: HelmServiceDep):
    try:
        out = service.uninstall(cluster_id, name, namespace)
    except HelmServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"status": "uninstalled", "output": out}
