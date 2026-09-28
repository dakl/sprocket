"""sprocket build doc.json --step out.step --stl out.stl --mesh out.mesh
sprocket serve

`build` is the one-shot CLI: write a document as JSON, run this command,
read the exported files back. `serve` is the same replay() call kept behind
a warm process: importing build123d (OCP's OpenCascade bindings) costs
2-3s, so a fresh subprocess per edit made the app's "rebuilding" step
sluggish. The Swift app now launches `serve` once and sends it one build
request per line instead of re-launching `build` on every op edit.
"""

import argparse
import json
import sys
from pathlib import Path

from build123d import export_step, export_stl

from .mesh import export_mesh
from .replay import ReplayError, replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sprocket", description="Replay a Sprocket document and export the result."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="replay a document and export STEP/STL")
    build.add_argument("document", type=Path, help="path to a document JSON file")
    build.add_argument("--step", type=Path, help="write STEP to this path")
    build.add_argument("--stl", type=Path, help="write STL to this path")
    build.add_argument(
        "--mesh", type=Path, help="write the viewer's tessellated mesh file to this path"
    )
    build.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="BREP cache directory (default: <document dir>/cache)",
    )

    sub.add_parser(
        "serve",
        help="read one JSON build request per line from stdin, keep build123d warm between builds",
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args)
    if args.command == "serve":
        return _serve()
    return 1


class DocumentError(Exception):
    """A document that cannot be read or makes no sense as a document, as
    opposed to one whose features no longer build. The second kind is normal
    editing (see _build_document); this kind is a bad path or a bad file."""


def _load_document(document_path: Path) -> dict:
    """Reads a document and checks the shape replay() and _last_solid_index()
    rely on. Both walk `features` and slice it by `rollback_index` without
    guarding, so a missing file or a `features` that is not a list used to come
    back as a traceback - past the error path that prints a readable message,
    and past `serve`, whose caller only ever sees `output`."""
    try:
        text = Path(document_path).read_text()
    except OSError as exc:
        raise DocumentError(f"cannot read document {document_path}: {exc.strerror}") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentError(f"{document_path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise DocumentError(f"{document_path}: a document must be a JSON object")

    features = document.get("features")
    if not isinstance(features, list) or not all(isinstance(feature, dict) for feature in features):
        raise DocumentError(f"{document_path}: 'features' must be a list of objects")

    rollback_index = document.get("rollback_index", len(features))
    if type(rollback_index) is not int or not 0 <= rollback_index <= len(features):
        raise DocumentError(
            f"{document_path}: 'rollback_index' must be an integer in 0..{len(features)}, "
            f"got {rollback_index!r}"
        )

    return document


def _last_solid_index(features: list[dict], rollback_index: int) -> int:
    """A sketch is a flat profile, not a solid (see ops/sketch.py) - it stays
    inert until something extrudes it. replay() only returns its last
    feature's result, so a bare sketch left at the tip would export as the
    part and blank out whatever solid came before it. Walk back past any
    trailing sketch features to the last one that actually produced a solid."""
    index = rollback_index
    while index > 0 and features[index - 1].get("type") == "sketch":
        index -= 1
    return index


def _build_document(
    document_path: Path,
    step: Path | None,
    stl: Path | None,
    cache_dir: Path | None,
    mesh: Path | None = None,
) -> tuple[bool, str, str | None]:
    """Replays one document and exports it. Shared by `build` and `serve`
    so the daemon path can't silently drift from the one-shot CLI path.

    Returns (success, message, failed_feature_id). A feature that no longer
    builds is not a failed build: editing a sketch can break a later op, and
    the user still needs to see the part up to that point. So the export falls
    back to the prefix before the broken feature, and its id comes back so the
    app can mark it."""
    try:
        document = _load_document(document_path)
    except DocumentError as exc:
        return False, f"error: {exc}", None
    resolved_cache_dir = Path(cache_dir) if cache_dir else Path(document_path).parent / "cache"

    features = document["features"]
    rollback_index = document.get("rollback_index", len(features))
    export_index = _last_solid_index(features, rollback_index)
    export_document = document if export_index == rollback_index else {**document, "rollback_index": export_index}

    failed_id = None
    try:
        result = replay(export_document, cache_dir=resolved_cache_dir)
    except Exception as exc:
        failed_index = getattr(exc, "feature_index", None)
        failed_id = getattr(exc, "feature_id", None)
        if failed_index is None:
            return False, f"error: {exc}", None
        good_index = _last_solid_index(features, failed_index)
        try:
            result = replay({**document, "rollback_index": good_index}, cache_dir=resolved_cache_dir)
        except Exception as earlier:
            return False, f"error: {earlier}", failed_id

    if result is None:
        # rollback_index=0: a valid empty document, not a failure. Clear any
        # stale export from before the rollback instead of leaving it behind.
        for path in (step, stl, mesh):
            if path and Path(path).exists():
                Path(path).unlink()
        return True, "replayed ok: empty document", failed_id

    messages = []
    if failed_id:
        messages.append(f"feature {failed_id!r} no longer builds, showing the part before it")
    if step:
        export_step(result, Path(step))
        messages.append(f"wrote {step}")
    if stl:
        export_stl(result, Path(stl))
        messages.append(f"wrote {stl}")
    if mesh:
        header = export_mesh(result, Path(mesh))
        counts = header["counts"]
        messages.append(
            f"wrote {mesh} ({counts['faces']} faces, {counts['triangles']} triangles, {counts['edges']} edges)"
        )
    if not step and not stl and not mesh:
        messages.append(f"replayed ok: volume={result.volume:.3f} mm^3")
    return True, "\n".join(messages), failed_id


def _build(args: argparse.Namespace) -> int:
    success, output, _ = _build_document(args.document, args.step, args.stl, args.cache_dir, args.mesh)
    if not success:
        print(output, file=sys.stderr)
        return 1
    print(output)
    return 0


def _serve() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            success, output, failed_id = _build_document(
                request["document"],
                request.get("step"),
                request.get("stl"),
                request.get("cache_dir"),
                request.get("mesh"),
            )
        except Exception as exc:
            success, output, failed_id = False, f"error: {exc}", None
        print(json.dumps({"success": success, "output": output, "failed_feature": failed_id}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
