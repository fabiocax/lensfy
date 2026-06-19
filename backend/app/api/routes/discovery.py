"""Dynamic API discovery: every resource type the cluster serves (built-in +
CRDs), so the Explorer tree can list anything installed (Istio Gateway/
VirtualService, Gateway API, cert-manager, ArgoCD, …) automatically."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.schemas.workloads import ManifestResponse
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("")
def discover(cluster_id: int, service: WorkloadServiceDep):
    """All listable resource types served by the cluster, grouped by API group."""
    try:
        return service.discover_resources(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/instances")
def list_instances(
    cluster_id: int, apiVersion: str, kind: str, service: WorkloadServiceDep,
    namespace: str | None = None,
):
    """List instances of any resource type (built-in or CRD) by apiVersion+kind."""
    try:
        return service.list_resource_dynamic(cluster_id, apiVersion, kind, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/manifest", response_model=ManifestResponse)
def get_instance_manifest(
    cluster_id: int, apiVersion: str, kind: str, name: str,
    service: WorkloadServiceDep, namespace: str | None = None,
):
    """Fetch a single instance as YAML by apiVersion+kind."""
    try:
        text = service.get_manifest_dynamic(cluster_id, apiVersion, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ManifestResponse(kind=kind, name=name, namespace=namespace, yaml=text)
