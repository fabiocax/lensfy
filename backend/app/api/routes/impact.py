"""Impact / blast-radius analysis (reverse dependency lookup).

Answers "where is this used?" (ConfigMap/Secret/PVC consumers) and "what breaks
if this goes?" (Node blast radius + single-point-of-failure workloads).
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.services.workloads import WorkloadServiceError

router = APIRouter()

_SUPPORTED = {"nodes", "configmaps", "secrets", "pvc"}


@router.get("")
def impact(
    cluster_id: int, kind: str, name: str, service: WorkloadServiceDep,
    namespace: str | None = None,
):
    if kind.lower() not in _SUPPORTED:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"análise de impacto não suportada para {kind} "
            f"(use: {', '.join(sorted(_SUPPORTED))})",
        )
    try:
        return service.impact(cluster_id, kind, name, namespace)
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
