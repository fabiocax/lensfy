from fastapi import APIRouter

from app.api.routes import (
    ai,
    clusters,
    deployments,
    helm,
    logs,
    metrics,
    onboarding,
    pods,
    portforward,
    resources,
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
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(
    onboarding.router, prefix="/onboarding", tags=["onboarding"]
)
