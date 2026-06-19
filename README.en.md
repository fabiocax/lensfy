# Lensfy

[Português](README.md) · **English**

> **Local Kubernetes cluster manager** — an open-source alternative to Lens/OpenLens that runs entirely on your machine, with no mandatory external services.

Multi-cluster, real-time logs and metrics, integrated terminal/`exec`, a `kubectl` shell, port-forward, manifest and Helm deploys, a YAML editor (Monaco) with version history, **security and RBAC auditing**, **capacity planning/rightsizing**, **global cross-cluster search**, **CRDs**, and an **AI assistant** (Claude API) that diagnoses problems and runs cluster operations — always with approval.

The UI is an **installable PWA**, served by the backend itself (FastAPI + Jinja2 + vanilla JS/CSS — **no build step, no npm**). Access is restricted to the local machine and protected by a **device token** (no login/password).

---

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Configuration](#configuration-environment-variables)
- [Security](#security)
- [First steps in the UI](#first-steps-in-the-ui)
- [Tests](#tests)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

### Multi-cluster
- **Import a kubeconfig** by path, file upload, or by pasting its contents — with context detection and a checklist (import several at once).
- **Import from Google Cloud (GKE):** the **gcloud** tab lists projects and clusters and runs `get-credentials` for you (requires `gcloud` + `gke-gcloud-auth-plugin`).
- **Cluster switcher** with search, per-item status/version, one-click switching, **drag-to-reorder**, and removal. Importing never blocks the UI (clusters come up in the background).
- **Per-cluster session:** when you return to a cluster, Lensfy restores where you left off — the view, the namespace filter, and the open dock tabs (logs/console/YAML/AI).
- **Global cross-cluster search:** find resources by name across **all** clusters at once, in parallel (unreachable clusters become a warning rather than failing the search); clicking a result switches cluster and opens the resource.
- **Cluster comparison** side by side (version, nodes, pods, deployments, usage) and an exportable **inventory** per cluster (counts per kind + pods per namespace).

### Resource explorer
- A tree with **Pods, Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Services, Ingress, NetworkPolicies, ConfigMaps, Secrets, PVC, StorageClasses, Namespaces, Nodes, Events, RBAC** (roles/bindings), **LimitRanges**, and **ResourceQuotas**.
- **Automatic resource discovery:** the sidebar lists **every resource type the cluster serves** (not just a fixed set) via *API discovery* — any installed CRD (Istio **Gateway/VirtualService**, **Gateway API** HTTPRoute, cert-manager, ArgoCD, Prometheus Operator…) shows up on its own, grouped by API group, with instance listing and YAML. Types already covered by dedicated views aren't duplicated.
- **CRDs / Custom Resources:** a dedicated view listing installed CRDs (group, kind, scope, versions) with drill-down into instances.
- **Live tables** (`/ws/watch`): pod creation/removal, status, and restarts update on their own — incremental reconciliation **without flicker** (selection and scroll preserved).
- **Global namespace filter** with multi-select (Lens-style) and a **global search / command palette** (focus with `/`).
- **Detail panel (drawer)** per resource: summary, metadata, status, containers (state/restarts/images), conditions, **live metrics** (CPU/mem), and events.

### Observability
- **Dashboard:** cluster health (nodes, versions), pod phases, restarts, available vs. desired deployments, CPU/memory usage, and warning events.
- **Metrics:** nodes/pods via `metrics.k8s.io`, summary cards, sortable columns, and threshold-colored bars.
- **Problems:** a cluster scan that lists issues by category and severity (CrashLoop, ImagePull, OOMKilled, pending, unbound PVC, nodes under pressure/cordoned, etc.).
- **Resources & Quotas:** sum of requests/limits per namespace, `ResourceQuota` used/limit, and containers **without requests/limits** (OOM/SLA risk).
- **Traffic map:** **Ingress → Service → Workload → Pods** topology rendered as SVG, with zoom/pan.
- **Capacity:** per node, allocatable vs. *requests* vs. live usage (scheduling headroom), with cluster totals and pod counts per node.
- **Rightsizing:** compares *requests/limits* to live usage (metrics-server) and recommends adjustments, flagging over/under-provisioning and OOM risk.

### Security & RBAC
- **Security scan (PSS-style):** detects `privileged` pods/containers, `hostNetwork/hostPID/hostIPC`, `hostPath` volumes, `runAsRoot`, dangerous *capabilities*, **missing limits**, mutable image tags, and auto-mounted SA tokens — grouped by rule/severity, with a **0–100 score**.
- **"Who can do what":** aggregates every RBAC subject (User/Group/ServiceAccount) and the verbs/resources granted by its bound roles, flagging **cluster-admins**.
- **`can-i` simulator:** authoritative permission check (SubjectAccessReview) for the current credential or a specific ServiceAccount/user.

### Impact analysis (blast radius) ⭐
**Reverse** dependency lookup — answers questions the Kubernetes API can't answer directly:
- **"Where is this ConfigMap/Secret/PVC used?"** — lists the pods that consume it **and how** (volume, projected volume, `envFrom`, `env`, `imagePullSecret`), aggregated by workload.
- **"What breaks if I drain this node?"** — a **Node**'s blast radius: the pods/workloads running on it and **SPOF** detection (workloads whose replicas all sit on the same node).
- Every result is clickable and opens the resource in the detail panel. Also available as an AI assistant tool.

### Real time (terminal, logs, console)
- **Live logs:** filter, auto-scroll, copy, download, and container selector.
- **Smart problem detection (live):** every line is classified in real time by heuristics (panic/crash, OOM, exceptions, network, timeout, auth, HTTP 5xx, DB errors, ERROR/WARN levels…), with **severity highlighting**, an **error/warning count**, and a **"problems only"** filter. Occurrences of the same problem are **grouped by signature** (timestamps/ids/IPs normalized) into a panel with a sample and count — and an **"Analyze"** button sends the summary to the AI assistant for root cause and fixes.
- **Terminal/console (xterm.js):** pod `exec` (PTY), **node shell** (Lens-style, via a privileged pod + `nsenter`), and a **`kubectl` shell** already scoped to the cluster context.
- **Lens-style bottom dock:** logs, console, YAML, and AI as **tabs**, several at once, in a resizable panel that pushes the view (it does not overlap it).

### YAML editor & deploy
- **YAML editor (Monaco)** to view/edit/apply any resource, with **diff** and **context-aware Kubernetes autocomplete**: suggests keys based on the document's `kind`, values only where they belong (`apiVersion`, `kind`, enums like `imagePullPolicy`/`type`/`protocol`/`pathType`/`accessModes`/`policyTypes`…), and skeleton snippets.
- **Version history (up to 5)** per resource, recorded on every *Apply*: load a version, **diff against the editor**, or **diff between two versions**.
- **Robust apply:** realigns `resourceVersion` to current state and retries on conflict (no intermittent save failures).
- **Manifest deploy:** Monaco editor with **15+ templates** (Deployment, StatefulSet, DaemonSet, HPA, PVC, NetworkPolicy, ServiceAccount…), a **Builder** (form → YAML), and drag-and-drop of YAML files/folders (multi-document). Namespace field with **autocomplete** from the cluster's namespaces, plus **copy/download/clear** buttons for the YAML.
- **Idempotent apply (server-side apply):** creates *or updates* (like `kubectl apply --server-side`) — re-applying an existing resource no longer fails.
- **Preview (diff):** server-side *dry-run* and a **field-level diff against live state** (like `kubectl diff`) before applying — shows what's new, what changes (current → new), and what stays the same.
- **Dry-run validation:** validates each document's schema/admission without creating anything.

### Operations
- **Workloads:** scale, *restart* (rollout), and delete.
- **Rollout:** revision history, *undo* (rollback), and *pause/resume*.
- **Nodes:** *cordon/uncordon* and *drain* (respecting PodDisruptionBudgets via the Eviction API).
- **CronJobs:** *trigger* (run now) and *suspend/resume*.
- **Resources:** edit per-container requests/limits; edit **Secrets/ConfigMaps** in-place.
- **Port-forward:** tunnels to pods, managed from the UI.
- **Helm:** releases, install/upgrade/rollback, and uninstall.

### AI assistant (optional)
- An SRE agent on the **Claude API** (Messages API, via `httpx` — no extra SDK): **read-only** tools (overview, list/view resources, logs, top, **security scan, RBAC `can-i`, capacity, rightsizing, CRDs, and impact analysis**) run automatically; **cluster-changing actions** (scale/restart/delete/cordon/drain/rollback/cronjob) require **Approve/Deny** in the UI.
- It can be limited to diagnose-only (`LENSFY_AI_ALLOW_MUTATIONS=false`), and diagnoses can be **saved as reports**.

### Platform
- **Local security with no login:** loopback-only access, a Host *allowlist* (anti DNS-rebinding), and a **device token**; an **onboarding** screen generates the token on first run.
- **Update notice:** the UI shows a (dismissible) banner when a newer version is on GitHub — it compares the installed commit (`source_ref`/HEAD) with the latest commit on the release branch. Apply it with `lensfy update`. The check is best-effort and cached; disable via `LENSFY_UPDATE_CHECK_ENABLED=false`.
- **Installable PWA** with an offline app shell.

---

## Requirements

- **Python 3.12+** (tested on 3.14).
- A **kubeconfig** with access to your clusters (`~/.kube/config` or imported in the UI).
- Optional — each feature degrades with a message when absent:
  - `kubectl` — for the header's kubectl shell.
  - `helm` — for the Helm tab.
  - `gcloud` (+ `gke-gcloud-auth-plugin`) — to import GKE clusters.
  - **metrics-server** in the cluster — for CPU/memory charts.
  - A **Claude API key** (`LENSFY_ANTHROPIC_API_KEY`) — for the AI assistant.

---

## Installation

### 1. Desktop installer (Linux) — recommended

Installs into an isolated venv, creates the **`lensfy`** command and an **app-menu shortcut** (no root):

```bash
git clone git@github.com:fabiocax/lensfy.git
cd lensfy
./install.sh                 # install/update (re-running updates in place)
./install.sh --service       # + systemd --user service (starts on login)
```

Then:

```bash
lensfy            # starts (if needed) and opens the browser
lensfy status     # state + health
lensfy stop       # stop
lensfy update     # fetch the latest version from GitHub and update in place
lensfy version    # show the installed version (source commit)
```

**Updating:** `lensfy update` clones the latest from
`github.com/fabiocax/lensfy`, reinstalls in place (preserving your data in
`~/.lensfy`) and restarts if it was running — including the systemd service when
configured. It's a no-op if already up to date (`--force` reinstalls anyway).
Origin and branch are configurable via `LENSFY_REPO` and `LENSFY_BRANCH`.
Requires `git`.

Or open **"Lensfy"** from the application menu. Installed layout:

| Path | Contents |
|---|---|
| `~/.local/share/lensfy/app` | code + UI |
| `~/.local/share/lensfy/venv` | dependencies |
| `~/.local/bin/lensfy` | launcher |
| `~/.local/share/applications/lensfy.desktop` | menu shortcut |
| `~/.local/state/lensfy/` | pid + log |
| `~/.lensfy/` | data (SQLite, token) — **preserved** |

Uninstall: `./uninstall.sh` (use `--purge` to also delete `~/.lensfy`).

### 2. .rpm package (Fedora/RHEL)

Builds a distributable `.rpm` with dependencies bundled in (**offline** install):

```bash
sudo dnf install -y rpm-build rpmdevtools python3-pip
./packaging/rpm/build-rpm.sh          # → packaging/rpm/dist/lensfy-<version>.rpm

sudo dnf install packaging/rpm/dist/lensfy-*.rpm
lensfy                                # or from the application menu
sudo dnf remove lensfy
```

> The bundled *wheels* are specific to the platform and Python version of the build host (e.g. x86_64 / Python 3.14). Build the package in an environment compatible with the target. Adjust the license in `packaging/rpm/lensfy.spec` (currently a placeholder).

### 3. From source (development)

```bash
git clone git@github.com:fabiocax/lensfy.git
cd lensfy/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Python 3.14:** if a package tries to build from source, force prebuilt wheels:
> `pip install --only-binary=:all: -r requirements.txt`

---

## Running

### Control scripts (from source)

From the project **root**:

```bash
./start.sh            # start in the background → http://127.0.0.1:8000
./lensfy.sh status    # state + health
./lensfy.sh logs      # follow the log
./lensfy.sh restart   # restart
./lensfy.sh update    # git pull + refresh dependencies + restart
./stop.sh             # stop (terminates the process group)
```

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
| `LENSFY_HOST` | `127.0.0.1` | Bind interface. **`0.0.0.0` requires `LENSFY_ALLOW_REMOTE=true` — see Security.** |
| `LENSFY_PORT` | `8000` | Port. |
| `LENSFY_RELOAD` | `0` | `1` for auto-reload (dev). |
| `LENSFY_DEBUG` | `false` | Creates the DB tables on startup (skips migrations in dev). |
| `LENSFY_DATABASE_URL` | sqlite at `~/.lensfy/lensfy.db` | DB override. |
| `LENSFY_CORS_ORIGINS` | `[]` | Extra CORS origins (e.g. the Tauri shell). |
| `LENSFY_SECURITY_ENABLED` | `true` | Toggles the local access control. |
| `LENSFY_ALLOW_REMOTE` | `false` | Allows non-loopback access (the token is still required). |
| `LENSFY_ALLOWED_HOSTS` | `[]` | Extra values accepted in the `Host` header. |
| `LENSFY_ANTHROPIC_API_KEY` | — | Enables the AI assistant (Claude API). |
| `LENSFY_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Assistant model. |
| `LENSFY_ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | API endpoint (override for a proxy/gateway). |
| `LENSFY_AI_ALLOW_MUTATIONS` | `false` | `true` allows cluster-changing actions (each still needs UI approval). Default: diagnose-only. |
| `LENSFY_UPDATE_CHECK_ENABLED` | `true` | Check GitHub for new versions and show the update banner. `false` disables it (no outbound call). |
| `LENSFY_UPDATE_REPO` | `fabiocax/lensfy` | Repository (owner/repo) queried for the update check. |
| `LENSFY_UPDATE_BRANCH` | `main` | Release branch compared during the check. |

Examples:

```bash
LENSFY_PORT=9000 ./start.sh
LENSFY_ANTHROPIC_API_KEY=sk-ant-... ./start.sh   # enable the AI assistant
```

---

## Security

Lensfy is a single-user local app, designed to be unreachable from other machines — **with no login or password**. Three layers, applied to every HTTP request **and** WebSocket:

1. **Loopback only** — connections outside `127.0.0.0/8`/`::1` are refused (even if the server is exposed by mistake).
2. **`Host` allowlist** — blocks *DNS-rebinding* attacks (a remote site resolving its domain to `127.0.0.1`).
3. **Device token** — generated once on the machine (`~/.lensfy/device_token`, permission `0600`) and required on `/api` and `/ws`. The SPA fetches it at runtime; a page from another origin can neither read nor forge it (this also defeats CSRF). On **first run**, an **onboarding** screen generates the token.

| Variable | Default | Effect |
|---|---|---|
| `LENSFY_SECURITY_ENABLED` | `true` | `false` disables all layers (trusted environment/tests). |
| `LENSFY_ALLOW_REMOTE` | `false` | `true` allows access beyond loopback (LAN) — **the token still applies**. Use with care. |
| `LENSFY_ALLOWED_HOSTS` | `[]` | Extra values accepted in the `Host` header (e.g. the machine hostname). |

Other notes:
- The AI assistant only runs cluster-changing actions **after explicit approval**; disable them with `LENSFY_AI_ALLOW_MUTATIONS=false`.
- Treat imported kubeconfigs as **trusted content** (they may carry credentials and `exec` commands).
- If you **regenerate/rotate** the token, **reload open tabs** (the UI shows "Invalid device session → Reload" when this happens).

---

## First steps in the UI

1. **(First run)** an **onboarding** screen generates this machine's token — click **"Generate token and start"**, then **"Enter"**.
2. **Import a cluster** — from the cluster switcher (top of the sidebar) → **"+ Import cluster"**:
   - **Path / File / Paste** a kubeconfig, or
   - **gcloud** → pick a project → list and import **GKE** clusters.
3. Browse the resource tree (Pods, Deployments, Services, Secrets, etc.).
4. The **Dashboard** shows cluster health; **Problems**, **Resources**, and **Map** sit at the top.
5. **AI Assistant** (🤖 button in the header) — ask for a diagnosis or an action; cluster-changing actions prompt for **Approve/Deny**.

### Install as an app (PWA)

In Chrome/Edge, click the install icon in the address bar (or the **Install** button in the header). It opens in its own window with a system icon.
> Service workers require a **secure context**: `localhost` (ok) or **HTTPS**.

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

## Project structure

```
lensfy/
├── lensfy.sh, start.sh, stop.sh   # application control (dev mode)
├── install.sh, uninstall.sh       # desktop installer (Linux, per-user)
├── packaging/
│   ├── lensfy                     # installed launcher
│   └── rpm/                       # spec + build-rpm.sh (.rpm package)
├── PROJECT.md                     # specification (pt-BR)
├── CLAUDE.md                      # architecture guide for contributors
└── backend/
    ├── app/
    │   ├── api/          # REST routes (/api): clusters, pods, deployments,
    │   │                 #   resources, logs, metrics, helm, portforward,
    │   │                 #   security, crds, capacity, impact, multicluster,
    │   │                 #   ai, onboarding
    │   ├── websocket/    # real-time channels (/ws): logs, terminal,
    │   │                 #   watch, events, metrics, ai, kubectl
    │   ├── services/     # business logic
    │   ├── repositories/ # data access
    │   ├── models/       # SQLAlchemy
    │   ├── kubernetes/   # Kubernetes SDK integration, helm, gcloud
    │   ├── ai/           # AI assistant (Claude Messages API via httpx)
    │   ├── core/         # config + security (device token)
    │   └── web/          # serves the UI (Jinja2) + PWA
    ├── templates/        # index.html (app shell)
    ├── static/           # css/, js/, icons/, manifest.webmanifest, sw.js
    ├── tests/
    └── requirements.txt
```

Layered architecture: `api/` → `services/` → `repositories/` → `models/`. Local persistence in SQLite (migrations via Alembic). Details in [`CLAUDE.md`](CLAUDE.md) and the spec in [`PROJECT.md`](PROJECT.md).

---

## Roadmap

- **`.deb` / AppImage** packaging and a native **desktop** wrapper (Tauri) for Linux/Windows/macOS.
- Token rotation from the UI.
- E2E tests (Playwright) and CI.

---

## License

Define the project's license (there's no `LICENSE` file in the repository yet). Also adjust the `License` field in `packaging/rpm/lensfy.spec`.
