from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str | None = None
    node: str | None = None
    ready: str | None = None  # e.g. "1/1"
    up: str | None = None  # uptime/age, e.g. "3d2h"
    restarts: int = 0
    containers: list[str] = []  # for the logs/terminal container picker


class DeploymentSummary(BaseModel):
    name: str
    namespace: str
    replicas: int = 0
    ready_replicas: int = 0
    available_replicas: int = 0


class ScaleRequest(BaseModel):
    replicas: int


class ContainerResourcesRequest(BaseModel):
    container: str
    requests: dict[str, str] = {}  # e.g. {"cpu": "100m", "memory": "128Mi"}
    limits: dict[str, str] = {}


class DataUpdateRequest(BaseModel):
    data: dict[str, str] = {}


class ClusterMetrics(BaseModel):
    nodes: int = 0
    namespaces: int = 0
    pods: int = 0
    deployments: int = 0
    services: int = 0
    ingresses: int = 0


class ResourceColumn(BaseModel):
    key: str
    label: str


class ResourceTable(BaseModel):
    """Generic column/row table for the read-only Explorer resource views."""

    kind: str
    namespaced: bool
    columns: list[ResourceColumn]
    rows: list[dict]


class ManifestResponse(BaseModel):
    kind: str
    name: str
    namespace: str | None = None
    yaml: str


class ManifestApply(BaseModel):
    yaml: str


class ManifestVersionSummary(BaseModel):
    """History list item — omits the YAML body to keep the listing light."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    size: int = 0  # length of the stored YAML, for display


class ManifestVersionRead(ManifestVersionSummary):
    kind: str
    name: str
    namespace: str = ""
    yaml: str


class DeployRequest(BaseModel):
    yaml: str
    namespace: str = "default"


class DeployResult(BaseModel):
    kind: str
    name: str
    namespace: str | None = None
    status: str  # created | error
    message: str | None = None


class DeployResponse(BaseModel):
    results: list[DeployResult]


class DataItem(BaseModel):
    key: str
    value: str


class ResourceData(BaseModel):
    kind: str
    name: str
    namespace: str | None = None
    type: str | None = None
    items: list[DataItem]


class PortForwardCreate(BaseModel):
    namespace: str
    pod: str
    remote_port: int
    local_port: int = 0  # 0 = pick a free port


class TopRow(BaseModel):
    name: str
    namespace: str | None = None
    cpu: int = 0  # millicores
    cpu_cap: int | None = None
    cpu_pct: int | None = None
    memory: int = 0  # MiB
    memory_cap: int | None = None
    memory_pct: int | None = None


class TopResponse(BaseModel):
    available: bool
    message: str | None = None
    rows: list[TopRow]


class PortForwardInfo(BaseModel):
    id: int
    cluster_id: int
    namespace: str
    pod: str
    remote_port: int
    local_port: int
    status: str


class HelmReleases(BaseModel):
    available: bool
    message: str | None = None
    releases: list[dict]


class HelmInstall(BaseModel):
    name: str
    chart: str
    namespace: str = "default"
    version: str | None = None
    repo: str | None = None


class HelmRollback(BaseModel):
    name: str
    namespace: str
    revision: int
