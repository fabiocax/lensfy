"""Server-rendered UI, following the Fluxy interface model: FastAPI serves
Jinja2 templates plus static CSS/JS (no separate SPA build step).

Templates live in ``backend/templates`` and assets in ``backend/static``;
``main.py`` mounts ``STATIC_DIR`` at /static.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings

# backend/app/web/router.py -> parents[2] == backend/
BASE_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _url_for(name: str, **params: str) -> str:
    """Flask-style ``url_for`` shim used by the templates (matches Fluxy).

    Static URLs get a ``?v=<mtime>`` cache-buster so browsers always fetch the
    current CSS/JS after a change (avoids stale-asset bugs).
    """
    if name == "static":
        filename = params.get("filename", "")
        try:
            version = int((STATIC_DIR / filename).stat().st_mtime)
        except OSError:
            version = 0
        return f"/static/{filename}?v={version}"
    return "/"


templates.env.globals["url_for"] = _url_for

web_router = APIRouter()


@web_router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    # Starlette >=0.29 signature: request first, then template name.
    # No-store on the shell so it always picks up the latest asset versions.
    # The device token is NOT embedded here — the SPA fetches it at startup from
    # GET /api/onboarding/token (so a cached PWA shell never carries a stale or
    # empty token). We only signal whether auth is on; first run (no token yet)
    # makes the SPA show the onboarding screen.
    response = templates.TemplateResponse(
        request,
        "index.html",
        {"security_enabled": get_settings().security_enabled},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# --- PWA: served from the root so the service worker controls the whole app ---


@web_router.get("/sw.js", include_in_schema=False)
def service_worker():
    # Root scope requires the SW file at "/"; allow it explicitly and never cache
    # the SW itself so updates roll out promptly.
    return FileResponse(
        STATIC_DIR / "js" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@web_router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )
