"""Custom Resource Definitions and their instances (dynamic discovery).

Lets the Explorer browse any CRD installed in the cluster (ArgoCD, cert-manager,
Istio, …) beyond the fixed built-in resource registry.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.schemas.workloads import ManifestResponse
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("")
def list_crds(cluster_id: int, service: WorkloadServiceDep):
    """List CustomResourceDefinitions installed in the cluster."""
    try:
        return service.list_crds(cluster_id)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/instances")
def list_instances(
    cluster_id: int, group: str, version: str, plural: str,
    service: WorkloadServiceDep, namespace: str | None = None,
):
    """List instances of a custom resource (name/namespace/age table)."""
    try:
        return service.list_custom_resource(cluster_id, group, version, plural, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/manifest", response_model=ManifestResponse)
def get_instance_manifest(
    cluster_id: int, group: str, version: str, plural: str, name: str,
    service: WorkloadServiceDep, namespace: str | None = None,
):
    """Fetch a single custom resource instance as YAML."""
    try:
        text = service.get_custom_resource(
            cluster_id, group, version, plural, name, namespace
        )
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ManifestResponse(kind=plural, name=name, namespace=namespace, yaml=text)
