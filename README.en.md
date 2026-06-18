# Lensfy

[Português](README.md) · **English**

**Local Kubernetes cluster manager** — an open-source alternative to Lens/OpenLens that runs entirely on your machine, with no mandatory external services.

Multi-cluster, real-time logs and metrics, integrated terminal/exec, a `kubectl` shell, port-forward, manifest/Helm deploys, a YAML editor (Monaco), and an **AI assistant** (Claude API) that diagnoses problems and automates cluster operations — with approval.

> The UI is an **installable PWA**, served by the backend itself (FastAPI + Jinja2 + vanilla JS/CSS — no build step, no npm).

---

## Requirements

- **Python 3.12+** (tested on 3.14).
- A **kubeconfig** with access to your clusters (`~/.kube/config` or imported in the UI).
- Optional (each feature degrades with a message when absent):
  - `kubectl` — for the header's kubectl shell.
  - `helm` — for the Helm tab.
  - `gcloud` (+ `gke-gcloud-auth-plugin`) — to import GKE clusters straight from Google Cloud.
  - **metrics-server** in the cluster — for CPU/memory charts.
  - A **Claude API key** (`LENSFY_ANTHROPIC_API_KEY`) — for the AI assistant.

---

## Installation

```bash
git clone https://gitlab.com/fabiocax/lensfy.git
cd lensfy/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Python 3.14:** if a package tries to build from source, force prebuilt wheels:
> `pip install --only-binary=:all: -r requirements.txt`

---

## Running

### Recommended — control scripts

From the project **root**:

```bash
./start.sh            # start in the background → http://127.0.0.1:8000
./lensfy.sh status    # state + health
./lensfy.sh logs      # follow the log
./lensfy.sh restart   # restart
./stop.sh             # stop (terminates the process group)
```

Open **http://localhost:8000** in your browser. `start` writes the PID/log under `backend/.run/` and waits for `/health` to respond before reporting it's up.

### Manual (development)

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

- App: <http://localhost:8000>
- API docs (Swagger): <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Editing files under `backend/templates/` or `backend/static/` only needs a **browser refresh** (no rebuild).

---

## Configuration (environment variables)

All prefixed with `LENSFY_`. See `backend/.env.example` (copy it to `backend/.env`).

| Variable | Default | Description |
|---|---|---|
| `LENSFY_HOST` | `127.0.0.1` | Bind interface. **`0.0.0.0` exposes it on the network — see Security.** |
| `LENSFY_PORT` | `8000` | Port. |
| `LENSFY_RELOAD` | `0` | `1` for auto-reload (dev), used by the scripts. |
| `LENSFY_DEBUG` | `false` | Creates the DB tables on startup (skips migrations in dev). |
| `LENSFY_DATABASE_URL` | sqlite at `~/.lensfy/lensfy.db` | DB override. |
| `LENSFY_ANTHROPIC_API_KEY` | — | Enables the AI assistant (Claude API). |
| `LENSFY_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Assistant model. |
| `LENSFY_AI_ALLOW_MUTATIONS` | `true` | `false` makes the AI diagnose-only (no cluster-changing actions). |

Examples:

```bash
LENSFY_PORT=9000 ./start.sh
LENSFY_ANTHROPIC_API_KEY=sk-ant-... ./start.sh   # enable the AI assistant
```

---

## First steps in the UI

1. **Import a cluster** — from the cluster switcher (top of the sidebar) → **“+ Importar cluster”**:
   - **Path / File / Paste** a kubeconfig, or
   - **gcloud** → pick a project → list and import **GKE** clusters.
2. Browse the resource tree (Pods, Deployments, Services, Secrets, etc.).
3. The **Dashboard** shows cluster health (nodes, pod phases, restarts, deployments, usage and warnings).
4. The **AI Assistant** (top of the sidebar) — ask for a diagnosis or an action; cluster-changing actions prompt for **Approve/Deny**.

### Install as an app (PWA)

In Chrome/Edge, click the install icon in the address bar (or the ⬇ button in the header). It opens in its own window with a system icon.
> Service workers require a **secure context**: `localhost` (ok) or **HTTPS**. On a remote host over plain HTTP, install isn't available.

---

## Tests

```bash
cd backend
source .venv/bin/activate
pytest                    # full suite
pytest --cov              # with coverage
pytest tests/test_ai.py   # a specific file
```

---

## Security ⚠️

- **Lensfy has no authentication.** Anyone who can reach the port controls every imported cluster (including reading secrets, running actions, and opening a shell on the host). Keep the bind on **`127.0.0.1`** (the default) and **do not** expose it on `0.0.0.0` without a protection layer (authenticated proxy / VPN).
- The AI assistant only runs cluster-changing actions **after explicit approval** in the UI; you can disable them with `LENSFY_AI_ALLOW_MUTATIONS=false`.
- Treat imported kubeconfigs as trusted content (they may carry credentials and `exec` commands).

---

## Structure

```
lensfy/
├── lensfy.sh, start.sh, stop.sh   # application control
├── PROJECT.md                     # specification (pt-BR)
├── CLAUDE.md                      # architecture guide for contributors
└── backend/
    ├── app/
    │   ├── api/          # REST routes (/api)
    │   ├── websocket/    # real-time channels (/ws): logs, terminal, watch, events, metrics, ai, kubectl
    │   ├── services/     # business logic
    │   ├── repositories/ # data access
    │   ├── models/       # SQLAlchemy
    │   ├── kubernetes/   # Kubernetes SDK integration, helm, gcloud
    │   ├── ai/           # AI assistant (Claude API)
    │   └── web/          # serves the UI (Jinja2) + PWA
    ├── templates/        # index.html (app shell)
    ├── static/           # css/, js/, icons/, manifest.webmanifest, sw.js
    ├── tests/
    └── requirements.txt
```

Layered architecture: `api/` → `services/` → `repositories/` → `models/`. Local persistence in SQLite (migrations via Alembic). Details in [`CLAUDE.md`](CLAUDE.md) and the spec in [`PROJECT.md`](PROJECT.md).

---

## Roadmap

- **Desktop** packaging (Tauri) for Linux/Windows/macOS.
- Local authentication (token).
- E2E tests (Playwright) and CI.
