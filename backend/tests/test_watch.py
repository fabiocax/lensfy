"""Contract test for the live-watch (/ws/watch) kind map."""

from app.kubernetes.client import KubernetesClient
from app.kubernetes.resources import RESOURCES


def test_watch_map_covers_registry_plus_pods_and_deployments():
    watchable = set(KubernetesClient._WATCH_METHODS)
    # Every read-only registry kind must be watchable so its list view goes live.
    assert set(RESOURCES) <= watchable
    # Pods and deployments have dedicated views but must also be watchable.
    assert {"pods", "deployments"} <= watchable
