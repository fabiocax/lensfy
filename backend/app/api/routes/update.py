"""Update availability check (compares the installed ref to GitHub)."""

from fastapi import APIRouter

from app.core.updates import check_update

router = APIRouter()


@router.get("/status")
def update_status(force: bool = False):
    """Whether a newer Lensfy is available on the configured GitHub branch.

    Best-effort and cached (~1h); ``force=true`` bypasses the cache.
    """
    return check_update(force=force)
