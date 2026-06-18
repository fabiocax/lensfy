from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# check_same_thread=False is required for SQLite under the threaded server.
_connect_args = (
    {"check_same_thread": False}
    if settings.resolved_database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.resolved_database_url,
    connect_args=_connect_args,
    future=True,
    pool_pre_ping=True,  # drop stale connections instead of handing them out
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables from model metadata (used in dev / tests)."""
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401
    from app.database.base import Base

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Add new columns to pre-existing SQLite tables (lightweight auto-migrate).

    create_all() never ALTERs an existing table, so on an older local DB new
    model columns must be added explicitly. SQLite-only; production uses Alembic.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    from sqlalchemy import inspect, text

    wanted = {
        "clusters": [
            ("insecure", "BOOLEAN DEFAULT 0"),
            ("color", "VARCHAR(7)"),
            ("sort_order", "INTEGER DEFAULT 0"),
        ]
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # Context is no longer unique (different kubeconfigs reuse context names).
        if inspector.has_table("clusters"):
            ctx_ix = next(
                (ix for ix in inspector.get_indexes("clusters")
                 if ix["column_names"] == ["context"] and ix["unique"]),
                None,
            )
            if ctx_ix:
                conn.execute(text(f'DROP INDEX IF EXISTS "{ctx_ix["name"]}"'))
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_clusters_context ON clusters(context)")
                )
