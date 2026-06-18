"""Generic read-only Explorer resources (nodes, services, jobs, events, …).

One endpoint backed by the kind registry in ``app.kubernetes.resources``.
Pods and Deployments keep their own richer endpoints (with actions); they are
also listable here for completeness.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import ManifestVersionServiceDep, WorkloadServiceDep
from app.kubernetes.resources import MANIFEST_KINDS, RESOURCE_KINDS
from app.schemas.workloads import (
    ContainerResourcesRequest,
    DataUpdateRequest,
    DeployRequest,
    DeployResponse,
    ManifestApply,
    ManifestResponse,
    ManifestVersionRead,
    ManifestVersionSummary,
    ResourceData,
    ResourceTable,
    ScaleRequest,
)
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("/kinds", response_model=list[str])
def list_kinds():
    """Resource kinds the Explorer can render."""
    return list(RESOURCE_KINDS)


@router.get("/detail")
def get_detail(
    cluster_id: int, kind: str, name: str, service: WorkloadServiceDep,
    namespace: str | None = None,
):
    """Full resource object (dict) for the detail panel."""
    if kind not in MANIFEST_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Kind desconhecido: {kind}")
    try:
        obj = service.get_object(cluster_id, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"kind": kind, "name": name, "namespace": namespace, "object": obj}


@router.get("/manifest", response_model=ManifestResponse)
def get_manifest(
    cluster_id: int,
    kind: str,
    name: str,
    service: WorkloadServiceDep,
    namespace: str | None = None,
):
    if kind not in MANIFEST_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Kind sem YAML: {kind}")
    try:
        text = service.get_manifest(cluster_id, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ManifestResponse(kind=kind, name=name, namespace=namespace, yaml=text)


@router.get("/data", response_model=ResourceData)
def get_resource_data(
    cluster_id: int, kind: str, name: str, namespace: str, service: WorkloadServiceDep,
):
    if kind not in ("secrets", "configmaps"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} não tem dados")
    try:
        return service.get_resource_data(cluster_id, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.put("/data")
def update_resource_data(
    cluster_id: int, kind: str, name: str, namespace: str,
    payload: DataUpdateRequest, service: WorkloadServiceDep,
):
    if kind not in ("secrets", "configmaps"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} não tem dados")
    try:
        service.update_resource_data(cluster_id, kind, name, namespace, payload.data)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "updated", "keys": len(payload.data)}


@router.post("/container-resources")
def set_container_resources(
    cluster_id: int, kind: str, name: str, namespace: str,
    payload: ContainerResourcesRequest, service: WorkloadServiceDep,
):
    try:
        service.set_container_resources(
            cluster_id, kind, name, namespace,
            payload.container, payload.requests, payload.limits,
        )
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "updated", "container": payload.container}


@router.post("/scale")
def scale_workload(
    cluster_id: int, kind: str, name: str, payload: ScaleRequest,
    service: WorkloadServiceDep, namespace: str,
):
    try:
        service.scale_workload(cluster_id, kind, name, namespace, payload.replicas)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "scaled", "replicas": payload.replicas}


@router.post("/restart")
def restart_workload(
    cluster_id: int, kind: str, name: str, service: WorkloadServiceDep, namespace: str,
):
    try:
        service.restart_workload(cluster_id, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "restarted"}


@router.post("/cronjob/trigger")
def trigger_cronjob(
    cluster_id: int, name: str, namespace: str, service: WorkloadServiceDep,
):
    try:
        job = service.trigger_cronjob(cluster_id, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "triggered", "job": job}


@router.post("/cronjob/suspend")
def suspend_cronjob(
    cluster_id: int, name: str, namespace: str, suspend: bool,
    service: WorkloadServiceDep,
):
    try:
        service.set_cronjob_suspend(cluster_id, name, namespace, suspend)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "suspended" if suspend else "resumed"}


@router.post("/node/cordon")
def cordon_node(
    cluster_id: int, name: str, service: WorkloadServiceDep, unschedulable: bool = True,
):
    try:
        service.cordon_node(cluster_id, name, unschedulable)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "cordoned" if unschedulable else "uncordoned"}


@router.post("/node/drain")
def drain_node(
    cluster_id: int, name: str, service: WorkloadServiceDep,
    grace_period: int | None = None,
):
    try:
        result = service.drain_node(cluster_id, name, grace_period)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "drained", **result}


@router.post("/rollout/pause")
def rollout_pause(
    cluster_id: int, kind: str, name: str, namespace: str, paused: bool,
    service: WorkloadServiceDep,
):
    try:
        service.rollout_pause(cluster_id, kind, name, namespace, paused)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "paused" if paused else "resumed"}


@router.get("/rollout/history")
def rollout_history(
    cluster_id: int, kind: str, name: str, namespace: str, service: WorkloadServiceDep,
):
    try:
        return {"revisions": service.rollout_history(cluster_id, kind, name, namespace)}
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/rollout/undo")
def rollout_undo(
    cluster_id: int, kind: str, name: str, namespace: str, revision: int,
    service: WorkloadServiceDep,
):
    try:
        service.rollout_undo(cluster_id, kind, name, namespace, revision)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"status": "rolledback", "revision": revision}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource(
    cluster_id: int, kind: str, name: str, service: WorkloadServiceDep,
    namespace: str | None = None,
):
    if kind not in MANIFEST_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Kind desconhecido: {kind}")
    try:
        service.delete_resource(cluster_id, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/validate", response_model=DeployResponse)
def validate_manifests(cluster_id: int, payload: DeployRequest, service: WorkloadServiceDep):
    """Server-side dry-run validation of a (multi-doc) YAML blob."""
    try:
        results = service.validate_manifests(cluster_id, payload.yaml, payload.namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DeployResponse(results=results)


@router.post("/deploy", response_model=DeployResponse)
def deploy_manifests(cluster_id: int, payload: DeployRequest, service: WorkloadServiceDep):
    """Create resources from a (multi-document) YAML blob via create_from_yaml."""
    try:
        results = service.deploy_manifests(cluster_id, payload.yaml, payload.namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DeployResponse(results=results)


@router.put("/manifest", response_model=ManifestResponse)
def apply_manifest(
    cluster_id: int,
    kind: str,
    name: str,
    payload: ManifestApply,
    service: WorkloadServiceDep,
    versions: ManifestVersionServiceDep,
    namespace: str | None = None,
):
    if kind not in MANIFEST_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Kind sem YAML: {kind}")
    try:
        text = service.apply_manifest(cluster_id, kind, name, namespace, payload.yaml)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    # Snapshot the applied YAML for rollback (best-effort; never fail the apply).
    try:
        versions.record(cluster_id, kind, name, namespace, text)
    except Exception:  # noqa: BLE001 — history is non-critical
        pass
    return ManifestResponse(kind=kind, name=name, namespace=namespace, yaml=text)


@router.get("/manifest/versions", response_model=list[ManifestVersionSummary])
def list_manifest_versions(
    cluster_id: int,
    kind: str,
    name: str,
    versions: ManifestVersionServiceDep,
    namespace: str | None = None,
):
    """Saved YAML versions for a resource, newest first (rollback history)."""
    return versions.list(cluster_id, kind, name, namespace)


@router.get("/manifest/versions/{version_id}", response_model=ManifestVersionRead)
def get_manifest_version(version_id: int, versions: ManifestVersionServiceDep):
    version = versions.get(version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versão não encontrada")
    return version


@router.delete(
    "/manifest/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_manifest_version(version_id: int, versions: ManifestVersionServiceDep):
    if not versions.delete(version_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versão não encontrada")


@router.get("", response_model=ResourceTable)
def list_resource(
    cluster_id: int,
    kind: str,
    service: WorkloadServiceDep,
    namespace: str | None = None,
):
    if kind not in RESOURCE_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown resource kind: {kind}")
    try:
        return service.list_resource(cluster_id, kind, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
