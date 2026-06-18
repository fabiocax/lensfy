"""Per-resource YAML version history (rollback for the manifest editor).

A snapshot is recorded each time a manifest is applied. Only the most recent
``MAX_VERSIONS`` are kept per (cluster_id, kind, namespace, name); older ones
are pruned. Cluster-scoped kinds use ``""`` for namespace.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.manifest_version import ManifestVersion


class ManifestVersionService:
    MAX_VERSIONS = 5

    def __init__(self, db: Session) -> None:
        self.db = db

    def _query(self, cluster_id: int, kind: str, name: str, namespace: str | None):
        return (
            select(ManifestVersion)
            .where(
                ManifestVersion.cluster_id == cluster_id,
                ManifestVersion.kind == kind,
                ManifestVersion.name == name,
                ManifestVersion.namespace == (namespace or ""),
            )
            .order_by(ManifestVersion.created_at.desc(), ManifestVersion.id.desc())
        )

    def list(
        self, cluster_id: int, kind: str, name: str, namespace: str | None
    ) -> list[ManifestVersion]:
        return list(self.db.scalars(self._query(cluster_id, kind, name, namespace)))

    def get(self, version_id: int) -> ManifestVersion | None:
        return self.db.get(ManifestVersion, version_id)

    def record(
        self, cluster_id: int, kind: str, name: str, namespace: str | None, yaml: str
    ) -> ManifestVersion | None:
        """Save a new version, deduping against the latest, then prune to MAX_VERSIONS."""
        existing = self.list(cluster_id, kind, name, namespace)
        # Skip if the newest version is byte-identical (re-apply without changes).
        if existing and existing[0].yaml == yaml:
            return existing[0]
        version = ManifestVersion(
            cluster_id=cluster_id,
            kind=kind,
            name=name,
            namespace=namespace or "",
            yaml=yaml,
        )
        self.db.add(version)
        self.db.flush()  # assign id/created_at before pruning
        for stale in existing[self.MAX_VERSIONS - 1 :]:
            self.db.delete(stale)
        self.db.commit()
        self.db.refresh(version)
        return version

    def delete(self, version_id: int) -> bool:
        version = self.get(version_id)
        if version is None:
            return False
        self.db.delete(version)
        self.db.commit()
        return True
