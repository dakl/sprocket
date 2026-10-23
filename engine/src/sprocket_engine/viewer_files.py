"""The files the viewer reads: part.mesh, features.json and schemas.json.

Two servers hand them to the viewer. `sprocket web` builds them on request. The
Swift app opens the viewer from disk, so `sprocket daemon --viewer-dir` writes
them next to it after every edit. Both go through these functions, so what the
viewer sees cannot depend on which one is running.
"""

import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

from build123d import Compound

from sprocket_engine.context import Body, Context
from sprocket_engine.document import Document, resolve_object
from sprocket_engine.mesh import export_mesh
from sprocket_engine.model import JSON, JSONObject, ThreadFeature, sketch_feature
from sprocket_engine.ops import REGISTRY
from sprocket_engine.ops.construct import construct_feature, construct_geometry
from sprocket_engine.ops.sketch import (
    plane_json,
    plane_ref,
    resolve_plane,
    sketch_entities,
    sketch_profiles,
)
from sprocket_engine.ops.thread import thread_geometry
from sprocket_engine.sketch_reference import has_references


class ViewerSession(Protocol):
    """What these functions read off the daemon's session (rpc.Session)."""

    @property
    def document(self) -> Document:
        """The open document."""
        ...

    def shape(self) -> tuple[Compound | None, str | None]:
        """The built part and the feature that failed, if one did."""
        ...

    def bodies(self) -> list[Body]:
        """The built bodies with their settings."""
        ...

    def context(self) -> Context:
        """The replay context of the current build."""
        ...


def features_summary(
    document: Document, failed_feature: str | None, ctx: Context | None = None
) -> list[dict[str, object]]:
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
    entries: list[dict[str, object]] = []
    for index, raw in enumerate(document.features):
        try:
            feature = resolve_object(raw, parameters)
        except Exception:
            feature = raw
        entry: dict[str, object] = {
            "id": raw.get("id"),
            "type": raw.get("type"),
            "active": index < document.rollback_index,
            "feature": raw,
            "resolved": feature,
        }
        active = index < document.rollback_index
        if feature.get("type") == "sketch":
            # A sketch with projected references is drawn as the build solved it around them.
            raw_id = raw.get("id")
            built = (
                ctx.features.get(raw_id)
                if ctx is not None and isinstance(raw_id, str)
                else None
            )
            if built is not None and has_references(built):
                feature = built
                entry["resolved"] = built
            entry["plane"] = _drawable_plane(feature.get("plane"), ctx)
            sketch = sketch_feature(feature)
            if feature.get("patterns") and sketch is not None:
                # Pattern copies to draw. The sketcher edits only the originals.
                try:
                    entry["entities"] = sketch_entities(sketch)
                except Exception:
                    pass
            # Only a polygon has corners the viewer can drag.
            if "polygon" in feature:
                entry["polygon"] = feature["polygon"]
            try:
                entry["regions"] = sketch_profiles(sketch) if sketch is not None else []
            except Exception:
                # A sketch the engine cannot read has no regions to show. The
                # timeline still has to list it so it can be fixed.
                entry["regions"] = []
        if feature.get("type") == "thread" and active and ctx is not None:
            # Where to draw a cosmetic thread. Nothing when it does not resolve:
            # the feature's own failure says why.
            try:
                thread = cast("ThreadFeature", cast("object", feature))
                entry["thread"] = thread_geometry(thread, ctx)
            except Exception:
                pass
        construct = construct_feature(feature)
        if construct is not None and active and ctx is not None:
            # Where to draw a plane, an axis or a point.
            try:
                entry["construct"] = construct_geometry(construct, ctx)
            except Exception:
                pass
        if feature.get("id") == failed_feature:
            entry["failed"] = True
        entries.append(entry)
    return entries


def _drawable_plane(plane: JSON, ctx: Context | None) -> JSON:
    """A name or an explicit plane as stored. An offset or face plane becomes
    explicit, because only the engine can find a face. None when it does not
    resolve, such as a sketch after the feature that broke the build.
    """
    if isinstance(plane, str) or (isinstance(plane, dict) and "origin" in plane):
        return plane
    try:
        resolved = plane_json(resolve_plane(plane_ref(plane), ctx))
    except Exception:
        return None
    return {
        "origin": [*resolved["origin"]],
        "normal": [*resolved["normal"]],
        "x_dir": [*resolved["x_dir"]],
    }


def mesh_bytes(
    shape: Compound | None,
    appearances: Sequence[JSONObject] | None = None,
    bodies: Sequence[Body] | None = None,
) -> bytes | None:
    """The viewer's mesh file for `shape`, or None for an empty document."""
    if shape is None and not bodies:
        return None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "part.mesh"
        _ = export_mesh(shape, path, appearances, bodies)
        return path.read_bytes()


def schemas() -> dict[str, JSONObject]:
    """Every op's JSON Schema, by op name."""
    return {name: op.schema for name, op in sorted(REGISTRY.items())}


def write_viewer_files(session: ViewerSession, viewer_dir: Path) -> str | None:
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
        viewer_dir / "features.json",
        json.dumps(
            features_summary(session.document, failed, session.context())
        ).encode(),
    )
    _write_atomically(viewer_dir / "schemas.json", json.dumps(schemas()).encode())
    return mesh_error


def _write_atomically(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    _ = temporary.write_bytes(data)
    _ = temporary.replace(path)
