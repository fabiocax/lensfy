# Lensfy Backend

FastAPI backend for Lensfy — local-first Kubernetes cluster manager.

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Health:   http://localhost:8000/health

## Database migrations

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

On startup in debug mode the app also creates tables automatically, so migrations
are optional during early development.

## Tests

```bash
pytest                      # full suite
pytest --cov                # with coverage (target > 85%)
pytest tests/test_clusters.py::test_create_cluster   # single test
```

## Layout

```
app/
├── api/          # HTTP routes (thin) -> services
├── services/     # business logic
├── repositories/ # data access (SQLAlchemy)
├── models/       # ORM models
├── schemas/      # Pydantic request/response models
├── websocket/    # /ws channels (logs, terminal, events, metrics)
├── kubernetes/   # kubernetes-python SDK wrapper
├── database/     # engine / session / declarative base
├── auth/         # (reserved) local auth
└── core/         # config, logging
```
