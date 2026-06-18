"""Snapshot log endpoint.

For live tailing use the WebSocket channel ``/ws/logs`` instead — this REST
route returns a single recent slice, matching ``GET /api/logs`` in the spec.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("")
def get_logs(
    cluster_id: int,
    name: str,
    namespace: str,
    service: WorkloadServiceDep,
    container: str | None = None,
    tail_lines: int = 200,
):
    try:
        client = service._client(cluster_id)
        text = client._core.read_namespaced_pod_log(
            name=name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
        )
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"name": name, "namespace": namespace, "logs": text}
