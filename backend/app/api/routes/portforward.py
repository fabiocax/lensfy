"""Port-forward tunnels: create/list/stop local tunnels to pod ports."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import WorkloadServiceDep
from app.kubernetes.portforward import manager
from app.schemas.workloads import PortForwardCreate, PortForwardInfo
from app.services.workloads import WorkloadServiceError

router = APIRouter()


@router.get("", response_model=list[PortForwardInfo])
def list_forwards():
    return manager.list()


@router.post("", response_model=PortForwardInfo, status_code=status.HTTP_201_CREATED)
def create_forward(cluster_id: int, payload: PortForwardCreate, service: WorkloadServiceDep):
    try:
        return service.start_port_forward(
            cluster_id, payload.namespace, payload.pod, payload.remote_port, payload.local_port
        )
    except WorkloadServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.delete("/{forward_id}", status_code=status.HTTP_204_NO_CONTENT)
def stop_forward(forward_id: int):
    if not manager.stop(forward_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Forward não encontrado")
