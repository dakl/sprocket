"""The files the viewer reads: part.mesh, features.json and schemas.json.

Two servers hand them to the viewer. `sprocket web` builds them on request. The
Swift app opens the viewer from disk, so `sprocket daemon --viewer-dir` writes
them next to it after every edit. Both go through these functions, so what the
viewer sees cannot depend on which one is running.
"""

import json
import tempfile
from pathlib import Path

from .mesh import export_mesh
from .ops import REGISTRY
from .ops.sketch import sketch_profiles


def features_summary(document, failed_feature: str | None) -> list[dict]:
    """The timeline as the viewer reads it: each entry carries its full feature."""
    entries = []
    for index, feature in enumerate(document.features):
        entry = {
            "id": feature.get("id"),
            "type": feature.get("type"),
            "active": index < document.rollback_index,
            "feature": feature,
        }
        if feature.get("type") == "sketch":
            entry["plane"] = feature["plane"]
            # Only a polygon has corners the viewer can drag.
            if "polygon" in feature:
                entry["polygon"] = feature["polygon"]
        if feature.get("type") == "sketch":
            try:
                entry["regions"] = sketch_profiles(feature)
            except Exception:
                # A sketch the engine cannot read has no regions to show. The
                # timeline still has to list it so it can be fixed.
                entry["regions"] = []
        if feature.get("id") == failed_feature:
            entry["failed"] = True
        entries.append(entry)
    return entries


def mesh_bytes(shape) -> bytes | None:
    """The viewer's mesh file for `shape`, or None for an empty document."""
    if shape is None:
        return None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "part.mesh"
        export_mesh(shape, path)
        return path.read_bytes()


def schemas() -> dict:
    return {name: op.schema for name, op in sorted(REGISTRY.items())}


def write_viewer_files(session, viewer_dir: Path) -> None:
    """Writes the three files for the session's current revision.

    An empty document deletes part.mesh, which the viewer reads as "nothing to
    show". Each file is written beside its final name and renamed, so a viewer
    fetching mid-write never reads half of one.
    """
    viewer_dir = Path(viewer_dir)
    shape, failed = session.shape()
    data = mesh_bytes(shape)
    mesh_path = viewer_dir / "part.mesh"
    if data is None:
        mesh_path.unlink(missing_ok=True)
    else:
        _write_atomically(mesh_path, data)
    _write_atomically(
        viewer_dir / "features.json", json.dumps(features_summary(session.document, failed)).encode()
    )
    _write_atomically(viewer_dir / "schemas.json", json.dumps(schemas()).encode())


def _write_atomically(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
