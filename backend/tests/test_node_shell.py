"""Node-shell pod lifecycle: surface actionable errors instead of a blank/45s hang.

Builds a KubernetesClient without __init__ (no kubeconfig needed) and injects a
fake CoreV1Api so we can drive the create/poll paths.
"""

from types import SimpleNamespace as NS

import pytest
from kubernetes.client.exceptions import ApiException

from app.kubernetes.client import KubernetesClient, KubernetesError


def _client(core):
    c = object.__new__(KubernetesClient)  # bypass __init__ (no kubeconfig)
    c._core = core
    return c


def _waiting_pod(reason, message="boom"):
    cs = NS(state=NS(waiting=NS(reason=reason, message=message), terminated=None))
    return NS(status=NS(phase="Pending", reason=None, container_statuses=[cs]))


def _api_exc(status, reason, body_message):
    import json
    e = ApiException(status=status, reason=reason)
    e.body = json.dumps({"message": body_message})
    return e


def test_create_node_shell_privileged_blocked_gives_hint():
    class Core:
        def create_namespaced_pod(self, ns, body):
            raise _api_exc(
                403, "Forbidden",
                'pods "x" is forbidden: violates PodSecurity "restricted": privileged',
            )

    c = _client(Core())
    with pytest.raises(KubernetesError) as ei:
        c._create_node_shell("node-1")
    msg = str(ei.value)
    assert "Pod Security" in msg and "privileg" in msg.lower()


def test_create_node_shell_image_pull_fails_fast():
    deleted = []

    class Core:
        def create_namespaced_pod(self, ns, body):
            return None

        def read_namespaced_pod(self, name, ns):
            return _waiting_pod("ImagePullBackOff", "can't pull alpine:3.20")

        def delete_namespaced_pod(self, name, namespace, grace_period_seconds=0):
            deleted.append(name)

    c = _client(Core())
    with pytest.raises(KubernetesError) as ei:
        c._create_node_shell("node-1")
    assert "ImagePullBackOff" in str(ei.value)
    assert deleted, "pod deve ser removido ao falhar"
