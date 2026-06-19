from fastapi import APIRouter

from app.api.routes import (
    ai,
    capacity,
    clusters,
    crds,
    deployments,
    discovery,
    helm,
    impact,
    logs,
    metrics,
    multicluster,
    onboarding,
    pods,
    portforward,
    resources,
    security,
    update,
)

api_router = APIRouter()
api_router.include_router(clusters.router, prefix="/clusters", tags=["clusters"])
api_router.include_router(pods.router, prefix="/pods", tags=["pods"])
api_router.include_router(
    deployments.router, prefix="/deployments", tags=["deployments"]
)
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(resources.router, prefix="/resources", tags=["resources"])
api_router.include_router(
    portforward.router, prefix="/portforward", tags=["portforward"]
)
api_router.include_router(helm.router, prefix="/helm", tags=["helm"])
api_router.include_router(security.router, prefix="/security", tags=["security"])
api_router.include_router(crds.router, prefix="/crds", tags=["crds"])
api_router.include_router(discovery.router, prefix="/discovery", tags=["discovery"])
api_router.include_router(capacity.router, prefix="/capacity", tags=["capacity"])
api_router.include_router(impact.router, prefix="/impact", tags=["impact"])
api_router.include_router(
    multicluster.router, prefix="/multicluster", tags=["multicluster"]
)
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(update.router, prefix="/update", tags=["update"])
api_router.include_router(
    onboarding.router, prefix="/onboarding", tags=["onboarding"]
)
