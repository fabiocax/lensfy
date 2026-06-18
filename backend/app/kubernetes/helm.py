"""Helm CLI wrapper (subprocess).

Each call runs the local ``helm`` binary against a cluster's kubeconfig context.
``available()`` lets callers degrade gracefully when helm isn't installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from app.core.logging import get_logger

logger = get_logger(__name__)


class HelmError(Exception):
    pass


def available() -> bool:
    return shutil.which("helm") is not None


def _run(cluster, args: list[str]) -> str:
    if not available():
        raise HelmError("Helm CLI não encontrado no sistema (instale o `helm`).")
    env = os.environ.copy()
    if cluster.kubeconfig_path:
        env["KUBECONFIG"] = cluster.kubeconfig_path
    cmd = ["helm", *args, "--kube-context", cluster.context]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise HelmError("helm expirou (timeout)") from exc
    except OSError as exc:
        raise HelmError(f"falha ao executar helm: {exc}") from exc
    if proc.returncode != 0:
        raise HelmError((proc.stderr or proc.stdout or "helm falhou").strip())
    return proc.stdout


def list_releases(cluster) -> list[dict]:
    out = _run(cluster, ["list", "--all-namespaces", "-o", "json"])
    return json.loads(out or "[]")


def uninstall(cluster, name: str, namespace: str) -> str:
    return _run(cluster, ["uninstall", name, "-n", namespace])


def rollback(cluster, name: str, namespace: str, revision: int) -> str:
    return _run(cluster, ["rollback", name, str(revision), "-n", namespace])


def install(cluster, name, chart, namespace, version=None, repo=None) -> str:
    args = ["install", name, chart, "-n", namespace, "--create-namespace"]
    if repo:
        args += ["--repo", repo]
    if version:
        args += ["--version", version]
    return _run(cluster, args)


def upgrade(cluster, name, chart, namespace, version=None, repo=None) -> str:
    args = ["upgrade", name, chart, "-n", namespace, "--install"]
    if repo:
        args += ["--repo", repo]
    if version:
        args += ["--version", version]
    return _run(cluster, args)
