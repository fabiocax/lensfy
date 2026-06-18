from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClusterBase(BaseModel):
    name: str
    context: str
    server: str | None = None
    provider: str | None = None
    favorite: bool = False


class KubeconfigSource(BaseModel):
    """Where to read a kubeconfig from: a file path or raw content."""

    kubeconfig_path: str | None = None
    kubeconfig_content: str | None = None


class KubeContext(BaseModel):
    name: str
    cluster: str | None = None
    server: str | None = None


class ClusterCreate(KubeconfigSource):
    """Import clusters from a kubeconfig path or raw content.

    ``contexts`` selects which contexts to import; ``context`` is the legacy
    single-select. When both are empty, every context is imported.
    """

    context: str | None = None
    contexts: list[str] = []
    insecure: bool = False


class GcloudClusterRef(BaseModel):
    """A GKE cluster to import (identified by name + location + project)."""

    name: str
    location: str
    project: str


class GcloudImport(BaseModel):
    clusters: list[GcloudClusterRef]
    insecure: bool = False


class ClusterUpdate(BaseModel):
    name: str | None = None
    favorite: bool | None = None
    insecure: bool | None = None
    color: str | None = None


class ClusterReorder(BaseModel):
    """New manual order as a list of cluster ids, first = top of the list."""

    order: list[int]


class ClusterRead(ClusterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str | None = None
    status: str
    insecure: bool = False
    color: str | None = None
    sort_order: int = 0
    kubeconfig_path: str | None = None
    created_at: datetime
    updated_at: datetime
