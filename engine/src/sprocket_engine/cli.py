"""sprocket build doc.sprocket --step out.step --stl out.stl --mesh out.mesh
sprocket serve
sprocket web doc.sprocket

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

from .cache import default_cache_dir
from .document import Document, DocumentError
from .export import ExportError, write_3mf, write_step, write_stl, write_stl_dir
from .mesh import export_mesh
from .replay import build_shape, with_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sprocket", description="Replay a Sprocket document and export the result."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="replay a document and export STEP/STL")
    build.add_argument("document", type=Path, help="path to a document JSON file")
    build.add_argument("--step", type=Path, help="write STEP to this path")
    build.add_argument("--stl", type=Path, help="write STL to this path, every body in one file")
    build.add_argument("--stl-dir", type=Path, help="write one STL per body into this existing directory")
    build.add_argument("--3mf", dest="three_mf", type=Path, help="write a 3MF with one object per body")
    build.add_argument(
        "--mesh", type=Path, help="write the viewer's tessellated mesh file to this path"
    )
    build.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="BREP cache directory (default: the user cache folder, see cache.default_cache_dir)",
    )

    sub.add_parser(
        "serve",
        help="read one JSON build request per line from stdin, keep build123d warm between builds",
    )

    daemon = sub.add_parser(
        "daemon", help="serve one document over a Unix socket, for the app, the viewer and agents"
    )
    daemon.add_argument("document", type=Path, help="path to a document JSON file")
    daemon.add_argument("--socket", type=Path, default=None, help="socket path (default: <document>.sock)")
    daemon.add_argument("--cache-dir", type=Path, default=None, help="BREP cache directory")
    daemon.add_argument(
        "--viewer-dir",
        type=Path,
        default=None,
        help="write part.mesh, features.json and schemas.json here after every change, for a viewer opened from disk",
    )
    daemon.add_argument(
        "--no-autosave",
        action="store_true",
        help="keep edits in memory instead of writing the document after each one",
    )
    daemon.add_argument(
        "--recovery",
        type=Path,
        default=None,
        help="with --no-autosave, copy unsaved edits here after every change, and delete the file once they are saved",
    )

    web = sub.add_parser(
        "web", help="serve one document to the viewer in a plain browser, and to agents over the daemon socket"
    )
    web.add_argument("document", type=Path, help="path to a document JSON file")
    web.add_argument("--port", type=int, default=8765, help="HTTP port on 127.0.0.1 (0 picks a free one)")
    web.add_argument("--socket", type=Path, default=None, help="daemon socket path (default: <document>.sock)")
    web.add_argument("--cache-dir", type=Path, default=None, help="BREP cache directory")
    web.add_argument("--viewer-dir", type=Path, default=None, help="the viewer's static files (default: the repo's viewer/)")
    web.add_argument(
        "--no-autosave",
        action="store_true",
        help="keep edits in memory instead of writing the document after each one",
    )

    sub.add_parser("schemas", help="print every op's JSON Schema, which the viewer builds its parameter forms from")

    mcp = sub.add_parser("mcp", help="expose a running daemon's document as MCP tools over stdio")
    mcp.add_argument("--socket", type=Path, help="socket path of a running daemon (default: the newest running app)")

    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args)
    if args.command == "serve":
        return _serve()
    if args.command == "daemon":
        return _daemon(args)
    if args.command == "web":
        return _web(args)
    if args.command == "schemas":
        return _schemas()
    if args.command == "mcp":
        return _mcp(args)
    return 1


def _build_document(
    document_path: Path,
    step: Path | None,
    stl: Path | None,
    cache_dir: Path | None,
    mesh: Path | None = None,
    stl_dir: Path | None = None,
    three_mf: Path | None = None,
) -> tuple[bool, str, str | None]:
    """Replays one document and exports it. Shared by `build` and `serve`
    so the daemon path can't silently drift from the one-shot CLI path.

    Returns (success, message, failed_feature_id). A feature that no longer
    builds is not a failed build: editing a sketch can break a later op, and
    the user still needs to see the part up to that point. So the export falls
    back to the prefix before the broken feature, and its id comes back so the
    app can mark it.

    The reading and checking is `Document.load`'s, not a second copy here. The
    daemon and this path have to agree on what counts as a document, and going
    through the same loader also means `build` resolves parameters the way the
    daemon does."""
    try:
        document = Document.load(document_path)
    except DocumentError as exc:
        return False, f"error: {exc}", None
    resolved_cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()

    replayable = document.for_replay()
    features = replayable["features"]
    rollback_index = replayable["rollback_index"]

    try:
        result, failed_id, context = build_shape(features, rollback_index, resolved_cache_dir)
    except Exception as exc:
        return False, f"error: {exc}", getattr(exc, "feature_id", None)

    if result is None:
        # rollback_index=0: a valid empty document, not a failure. Clear any
        # stale export from before the rollback instead of leaving it behind.
        for path in (step, stl, mesh):
            if path and Path(path).exists():
                Path(path).unlink()
        return True, "replayed ok: empty document", failed_id

    bodies = with_settings(context.bodies, document.bodies)
    messages = []
    if failed_id:
        messages.append(f"feature {failed_id!r} no longer builds, showing the part before it")
    try:
        if step:
            write_step(result, step, bodies)
            messages.append(f"wrote {step}")
        if stl:
            write_stl(result, stl)
            messages.append(f"wrote {stl}")
        if stl_dir:
            messages.extend(f"wrote {path}" for path in write_stl_dir(bodies, stl_dir))
        if three_mf:
            write_3mf(bodies, three_mf)
            messages.append(f"wrote {three_mf}")
        header = export_mesh(result, Path(mesh), document.appearances, bodies) if mesh else None
    except ExportError as exc:
        return False, f"error: {exc}", failed_id
    except OSError as exc:
        return False, f"error: cannot write {exc.filename}: {exc.strerror}", failed_id
    if header:
        counts = header["counts"]
        messages.append(
            f"wrote {mesh} ({counts['faces']} faces, {counts['triangles']} triangles, {counts['edges']} edges)"
        )
    if len(bodies) > 1:
        messages.append(f"{len(bodies)} bodies: {', '.join(body['name'] for body in bodies)}")
    if not any((step, stl, mesh, stl_dir, three_mf)):
        messages.append(f"replayed ok: volume={result.volume:.3f} mm^3")
    return True, "\n".join(messages), failed_id


def _build(args: argparse.Namespace) -> int:
    success, output, _ = _build_document(
        args.document, args.step, args.stl, args.cache_dir, args.mesh, args.stl_dir, args.three_mf
    )
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


def _daemon(args: argparse.Namespace) -> int:
    # Imported here, not at module scope, so `sprocket build` keeps starting
    # without paying for the socket machinery it never uses.
    from .rpc import Daemon, Session

    try:
        document = Document.load(args.document)
    except DocumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    socket_path = args.socket or Path(str(args.document) + ".sock")
    session = Session(document, args.cache_dir)
    server = Daemon(
        session, socket_path, autosave=not args.no_autosave, viewer_dir=args.viewer_dir, recovery_path=args.recovery
    )
    server.publish_viewer_files()
    # A script, `sprocket build`, git or another editor can rewrite the file
    # while the app has it open. See Daemon.check_file for the rule.
    server.start_watching()
    print(f"listening on {socket_path} (revision {document.revision})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def _schemas() -> int:
    from .ops import REGISTRY

    print(json.dumps({name: op.schema for name, op in sorted(REGISTRY.items())}, indent=2))
    return 0


def _web(args: argparse.Namespace) -> int:
    from .rpc import Daemon, Session
    from .web import WebApp, make_server

    try:
        document = Document.load(args.document)
    except DocumentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    viewer_dir = args.viewer_dir or Path(__file__).resolve().parents[3] / "viewer"
    if not (viewer_dir / "index.html").exists():
        print(f"error: no viewer at {viewer_dir}: pass --viewer-dir", file=sys.stderr)
        return 1

    socket_path = args.socket or Path(str(args.document) + ".sock")
    daemon = Daemon(Session(document, args.cache_dir), socket_path, autosave=not args.no_autosave)
    daemon.start_in_background()
    daemon.start_watching()
    server = make_server(WebApp(daemon, viewer_dir), args.port)
    print(f"serving http://127.0.0.1:{server.server_address[1]}/ (agents attach at {socket_path})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        daemon.shutdown()
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run

    return run(args.socket)


if __name__ == "__main__":
    raise SystemExit(main())
