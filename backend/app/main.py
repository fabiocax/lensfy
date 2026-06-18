from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import LocalSecurityMiddleware
from app.database.session import init_db
from app.web import STATIC_DIR, web_router
from app.websocket import ws_router

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    # Raise the worker-thread limit. Every live WebSocket stream (logs/watch/
    # events) parks one thread on a blocking next(), and FastAPI runs sync route
    # handlers on this same pool — anyio's default of 40 lets a handful of open
    # streams starve the REST API (and /health). 256 is plenty for a local app.
    try:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 256
    except Exception:  # noqa: BLE001 - non-fatal tuning
        logger.warning("could not raise thread limiter")
    # Auto-create tables in dev; production should rely on Alembic migrations.
    if settings.debug:
        init_db()
    logger.info("%s v%s started", settings.app_name, settings.version)
    yield


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last => outermost: local-only access control runs before routing/CORS,
# so remote/cross-origin requests are rejected before touching any handler.
app.add_middleware(LocalSecurityMiddleware)


@app.middleware("http")
async def no_store_api(request, call_next):
    """API responses are live data — forbid browser HTTP caching.

    Without this a GET like /api/onboarding/token gets heuristically cached and
    a stale body (e.g. an early ``{"token": null}``) is replayed on later loads.
    """
    response = await call_next(request)
    if request.url.path.startswith(settings.api_prefix):
        response.headers["Cache-Control"] = "no-store"
    return response

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(ws_router)

# Server-rendered UI (Jinja2 + static assets), Fluxy-style.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(web_router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "version": settings.version}
