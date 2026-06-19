from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.services.ai_reports import AIReportService
from app.services.cluster import ClusterService
from app.services.helm import HelmService
from app.services.manifest_versions import ManifestVersionService
from app.services.multicluster import MultiClusterService
from app.services.workloads import WorkloadService

DbSession = Annotated[Session, Depends(get_db)]


def get_cluster_service(db: DbSession) -> ClusterService:
    return ClusterService(db)


def get_ai_report_service(db: DbSession) -> AIReportService:
    return AIReportService(db)


def get_workload_service(db: DbSession) -> WorkloadService:
    return WorkloadService(db)


def get_helm_service(db: DbSession) -> HelmService:
    return HelmService(db)


def get_manifest_version_service(db: DbSession) -> ManifestVersionService:
    return ManifestVersionService(db)


def get_multicluster_service(db: DbSession) -> MultiClusterService:
    return MultiClusterService(db)


ClusterServiceDep = Annotated[ClusterService, Depends(get_cluster_service)]
WorkloadServiceDep = Annotated[WorkloadService, Depends(get_workload_service)]
HelmServiceDep = Annotated[HelmService, Depends(get_helm_service)]
AIReportServiceDep = Annotated[AIReportService, Depends(get_ai_report_service)]
ManifestVersionServiceDep = Annotated[
    ManifestVersionService, Depends(get_manifest_version_service)
]
MultiClusterServiceDep = Annotated[
    MultiClusterService, Depends(get_multicluster_service)
]
