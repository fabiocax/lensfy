"""Google Cloud SDK (gcloud) wrapper for importing GKE clusters.

Shells out to the local ``gcloud`` binary to list projects / GKE clusters and to
write kubeconfig credentials (``get-credentials``). ``status()`` lets the UI
degrade gracefully when gcloud (or the GKE auth plugin) isn't installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


class GcloudError(Exception):
    pass


def available() -> bool:
    return shutil.which("gcloud") is not None


def auth_plugin_available() -> bool:
    """GKE kubeconfigs use ``gke-gcloud-auth-plugin`` (a separate component)."""
    if shutil.which("gke-gcloud-auth-plugin"):
        return True
    # Also look next to the gcloud binary (SDK bin dir).
    gcloud = shutil.which("gcloud")
    if gcloud:
        cand = Path(gcloud).resolve().parent / "gke-gcloud-auth-plugin"
        if cand.exists():
            return True
    return False


def status() -> dict:
    if not available():
        return {
            "available": False,
            "plugin": False,
            "message": "gcloud não encontrado no sistema. Instale o Google Cloud SDK.",
        }
    plugin = auth_plugin_available()
    msg = None
    if not plugin:
        msg = (
            "gke-gcloud-auth-plugin não instalado — os clusters serão importados, "
            "mas a conexão só funcionará após `gcloud components install "
            "gke-gcloud-auth-plugin`."
        )
    return {"available": True, "plugin": plugin, "message": msg}


def _run(args: list[str], timeout: int = 60, kubeconfig: str | None = None) -> str:
    if not available():
        raise GcloudError("gcloud não encontrado no sistema (instale o Google Cloud SDK).")
    env = os.environ.copy()
    if kubeconfig:
        env["KUBECONFIG"] = kubeconfig
    # get-credentials needs USE_GKE_GCLOUD_AUTH_PLUGIN=True to write the modern
    # exec auth (default on recent gcloud, set explicitly for older ones).
    env.setdefault("USE_GKE_GCLOUD_AUTH_PLUGIN", "True")
    try:
        proc = subprocess.run(
            ["gcloud", *args], capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise GcloudError("gcloud expirou (timeout)") from exc
    except OSError as exc:
        raise GcloudError(f"falha ao executar gcloud: {exc}") from exc
    if proc.returncode != 0:
        raise GcloudError((proc.stderr or proc.stdout or "gcloud falhou").strip())
    return proc.stdout


def list_projects() -> list[dict]:
    out = _run(
        ["projects", "list", "--format=json", "--sort-by=projectId"], timeout=60
    )
    data = json.loads(out or "[]")
    return [
        {"project": p["projectId"], "name": p.get("name") or p["projectId"]}
        for p in data
    ]


def list_clusters(project: str) -> list[dict]:
    out = _run(
        ["container", "clusters", "list", "--project", project, "--format=json"],
        timeout=90,
    )
    data = json.loads(out or "[]")
    return [
        {
            "name": c["name"],
            "location": c.get("location"),
            "project": project,
            "status": c.get("status"),
            "version": c.get("currentMasterVersion"),
            "nodes": c.get("currentNodeCount"),
            "endpoint": c.get("endpoint"),
        }
        for c in data
    ]


def get_credentials(name: str, location: str, project: str, kubeconfig_path: str) -> str:
    """Run ``get-credentials`` into ``kubeconfig_path``; return the context name.

    The generated context is read back from the file's ``current-context`` so we
    don't have to predict the ``gke_<project>_<loc>_<name>`` naming.
    """
    if name.startswith("-"):  # positional arg helm/gcloud would parse as a flag
        raise GcloudError(f"nome de cluster inválido: {name!r}")
    _run(
        [
            "container", "clusters", "get-credentials", name,
            "--location", location, "--project", project,
        ],
        timeout=90,
        kubeconfig=kubeconfig_path,
    )
    try:
        doc = yaml.safe_load(Path(kubeconfig_path).read_text(encoding="utf-8")) or {}
        ctx = doc.get("current-context")
    except OSError as exc:  # pragma: no cover - file just written
        raise GcloudError(f"falha ao ler kubeconfig gerado: {exc}") from exc
    if not ctx:
        raise GcloudError("get-credentials não definiu um contexto")
    return ctx
