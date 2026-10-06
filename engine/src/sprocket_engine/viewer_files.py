"""The files the viewer reads: part.mesh, features.json and schemas.json.

Two servers hand them to the viewer. `sprocket web` builds them on request. The
Swift app opens the viewer from disk, so `sprocket daemon --viewer-dir` writes
them next to it after every edit. Both go through these functions, so what the
viewer sees cannot depend on which one is running.
"""

import json
import tempfile
from pathlib import Path

from .document import resolve_value
from .mesh import export_mesh
from .ops import REGISTRY
from .ops.sketch import plane_json, resolve_plane, sketch_entities, sketch_profiles
from .ops.thread import thread_geometry


def features_summary(document, failed_feature: str | None, ctx=None) -> list[dict]:
    """The timeline as the viewer reads it.

    Each entry carries its full feature twice. `feature` is the raw form, with
    `"=param"` strings, which the parameter form edits. `resolved` holds only
    numbers, which is what the viewer draws from: a string coordinate is NaN in
    three.js, and the region finder cannot read one either.
    """
    try:
        parameters = document.resolved_parameters()
    except Exception:
        parameters = {}
    entries = []
    for index, raw in enumerate(document.features):
        try:
            feature = resolve_value(raw, parameters)
        except Exception:
            feature = raw
        entry = {
            "id": raw.get("id"),
            "type": raw.get("type"),
            "active": index < document.rollback_index,
            "feature": raw,
            "resolved": feature,
        }
        if feature.get("type") == "sketch":
            entry["plane"] = _drawable_plane(feature["plane"], ctx)
            if feature.get("patterns"):
                # Pattern copies to draw. The sketcher edits only the originals.
                try:
                    entry["entities"] = sketch_entities(feature)
                except Exception:
                    pass
            # Only a polygon has corners the viewer can drag.
            if "polygon" in feature:
                entry["polygon"] = feature["polygon"]
            try:
                entry["regions"] = sketch_profiles(feature)
            except Exception:
                # A sketch the engine cannot read has no regions to show. The
                # timeline still has to list it so it can be fixed.
                entry["regions"] = []
        if feature.get("type") == "thread" and entry["active"] and ctx is not None:
            # Where to draw a cosmetic thread. Nothing when it does not resolve:
            # the feature's own failure says why.
            try:
                entry["thread"] = thread_geometry(feature, ctx)
            except Exception:
                pass
        if feature.get("id") == failed_feature:
            entry["failed"] = True
        entries.append(entry)
    return entries


def _drawable_plane(plane, ctx):
    """A name or an explicit plane as stored. An offset or face plane becomes
    explicit, because only the engine can find a face. None when it does not
    resolve, such as a sketch after the feature that broke the build."""
    if isinstance(plane, str) or "origin" in plane:
        return plane
    try:
        return plane_json(resolve_plane(plane, ctx))
    except Exception:
        return None


def mesh_bytes(shape, appearances=None, bodies=None) -> bytes | None:
    """The viewer's mesh file for `shape`, or None for an empty document."""
    if shape is None and not bodies:
        return None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "part.mesh"
        export_mesh(shape, path, appearances, bodies)
        return path.read_bytes()


def schemas() -> dict:
    return {name: op.schema for name, op in sorted(REGISTRY.items())}


def write_viewer_files(session, viewer_dir: Path) -> str | None:
    """Writes the three files for the session's current revision.

    An empty document deletes part.mesh, which the viewer reads as "nothing to
    show". Each file is written beside its final name and renamed, so a viewer
    fetching mid-write never reads half of one.

    A part that builds but cannot be written as a mesh also deletes part.mesh,
    because the old file belongs to another revision and would show the wrong
    part. The timeline is still written, and the reason comes back as text for
    the viewer to show. Returns None when the mesh was written.
    """
    viewer_dir = Path(viewer_dir)
    shape, failed = session.shape()
    mesh_error = None
    try:
        data = mesh_bytes(shape, session.document.appearances, session.bodies())
    except Exception as exc:
        data = None
        mesh_error = f"{type(exc).__name__}: {exc}"
    mesh_path = viewer_dir / "part.mesh"
    if data is None:
        mesh_path.unlink(missing_ok=True)
    else:
        _write_atomically(mesh_path, data)
    _write_atomically(
        viewer_dir / "features.json", json.dumps(features_summary(session.document, failed, session.context())).encode()
    )
    _write_atomically(viewer_dir / "schemas.json", json.dumps(schemas()).encode())
    return mesh_error


def _write_atomically(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
