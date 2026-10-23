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

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.export import (
    ExportError,
    write_3mf,
    write_step,
    write_stl,
    write_stl_dir,
)
from sprocket_engine.mesh import MeshHeader, export_mesh
from sprocket_engine.model import JSON, parse_json
from sprocket_engine.replay import build_shape, failed_feature, with_settings


def _out(text: str) -> None:
    """A line of the command's own output, on stdout."""
    _ = sys.stdout.write(f"{text}\n")
    _ = sys.stdout.flush()


def _err(text: str) -> None:
    """A line about a failure, on stderr."""
    _ = sys.stderr.write(f"{text}\n")


# argparse gives every argument as Any. These read one, and say what it is.


def _path(args: argparse.Namespace, name: str) -> Path | None:
    value: object = getattr(args, name, None)
    if value is None or isinstance(value, Path):
        return value
    raise TypeError(f"--{name} is not a path")


def _required_path(args: argparse.Namespace, name: str) -> Path:
    value = _path(args, name)
    if value is None:
        raise TypeError(f"{name} is required")
    return value


def _flag(args: argparse.Namespace, name: str) -> bool:
    value: object = getattr(args, name, False)
    return bool(value)


def _number(args: argparse.Namespace, name: str) -> int:
    value: object = getattr(args, name, None)
    if not isinstance(value, int):
        raise TypeError(f"--{name} is not a number")
    return value


def main(argv: list[str] | None = None) -> int:
    """Run one `sprocket` command and return its exit code."""
    parser = argparse.ArgumentParser(
        prog="sprocket", description="Replay a Sprocket document and export the result."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="replay a document and export STEP/STL")
    _ = build.add_argument("document", type=Path, help="path to a document JSON file")
    _ = build.add_argument("--step", type=Path, help="write STEP to this path")
    _ = build.add_argument(
        "--stl", type=Path, help="write STL to this path, every body in one file"
    )
    _ = build.add_argument(
        "--stl-dir",
        type=Path,
        help="write one STL per body into this existing directory",
    )
    _ = build.add_argument(
        "--3mf", dest="three_mf", type=Path, help="write a 3MF with one object per body"
    )
    _ = build.add_argument(
        "--mesh",
        type=Path,
        help="write the viewer's tessellated mesh file to this path",
    )
    _ = build.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="BREP cache directory (default: <document dir>/cache)",
    )

    _ = sub.add_parser(
        "serve",
        help="read one JSON build request per line from stdin, keep build123d warm between builds",
    )

    daemon = sub.add_parser(
        "daemon",
        help="serve one document over a Unix socket, for the app, the viewer and agents",
    )
    _ = daemon.add_argument("document", type=Path, help="path to a document JSON file")
    _ = daemon.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="socket path (default: <document>.sock)",
    )
    _ = daemon.add_argument(
        "--cache-dir", type=Path, default=None, help="BREP cache directory"
    )
    _ = daemon.add_argument(
        "--viewer-dir",
        type=Path,
        default=None,
        help="write part.mesh, features.json and schemas.json here after every change, for a viewer opened from disk",
    )
    _ = daemon.add_argument(
        "--no-autosave",
        action="store_true",
        help="keep edits in memory instead of writing the document after each one",
    )
    _ = daemon.add_argument(
        "--recovery",
        type=Path,
        default=None,
        help="with --no-autosave, copy unsaved edits here after every change, and delete the file once they are saved",
    )

    web = sub.add_parser(
        "web",
        help="serve one document to the viewer in a plain browser, and to agents over the daemon socket",
    )
    _ = web.add_argument("document", type=Path, help="path to a document JSON file")
    _ = web.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP port on 127.0.0.1 (0 picks a free one)",
    )
    _ = web.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="daemon socket path (default: <document>.sock)",
    )
    _ = web.add_argument(
        "--cache-dir", type=Path, default=None, help="BREP cache directory"
    )
    _ = web.add_argument(
        "--viewer-dir",
        type=Path,
        default=None,
        help="the viewer's static files (default: the repo's viewer/)",
    )
    _ = web.add_argument(
        "--no-autosave",
        action="store_true",
        help="keep edits in memory instead of writing the document after each one",
    )

    _ = sub.add_parser(
        "schemas",
        help="print every op's JSON Schema, which the viewer builds its parameter forms from",
    )

    mcp = sub.add_parser(
        "mcp", help="expose a running daemon's document as MCP tools over stdio"
    )
    _ = mcp.add_argument(
        "--socket",
        type=Path,
        help="socket path of a running daemon (default: the newest running app)",
    )

    args = parser.parse_args(argv)
    command: object = getattr(args, "command", None)
    if command == "build":
        return _build(args)
    if command == "serve":
        return serve()
    if command == "daemon":
        return _daemon(args)
    if command == "web":
        return _web(args)
    if command == "schemas":
        return _schemas()
    if command == "mcp":
        return _mcp(args)
    return 1


def build_document(
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
    daemon does.
    """
    try:
        document = Document.load(document_path)
    except DocumentError as exc:
        return False, f"error: {exc}", None
    resolved_cache_dir = (
        Path(cache_dir) if cache_dir else Path(document_path).parent / "cache"
    )

    features = document.for_replay()["features"]

    try:
        result, failed_id, context = build_shape(
            features, document.rollback_index, resolved_cache_dir
        )
    except Exception as exc:
        failed = failed_feature(exc)
        return False, f"error: {exc}", failed.feature_id if failed else None

    if result is None:
        # rollback_index=0: a valid empty document, not a failure. Clear any
        # stale export from before the rollback instead of leaving it behind.
        for path in (step, stl, mesh):
            if path and Path(path).exists():
                Path(path).unlink()
        return True, "replayed ok: empty document", failed_id

    bodies = with_settings(context.bodies, {**document.bodies})
    messages: list[str] = []
    if failed_id:
        messages.append(
            f"feature {failed_id!r} no longer builds, showing the part before it"
        )
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
        header: MeshHeader | None = (
            export_mesh(result, Path(mesh), document.appearances, bodies)
            if mesh
            else None
        )
    except ExportError as exc:
        return False, f"error: {exc}", failed_id
    except OSError as exc:
        filename: object = getattr(exc, "filename", None)
        return False, f"error: cannot write {filename}: {exc.strerror}", failed_id
    if header:
        counts = header["counts"]
        messages.append(
            f"wrote {mesh} ({counts['faces']} faces, {counts['triangles']} triangles, {counts['edges']} edges)"
        )
    if len(bodies) > 1:
        messages.append(
            f"{len(bodies)} bodies: {', '.join(body.name for body in bodies)}"
        )
    if not any((step, stl, mesh, stl_dir, three_mf)):
        messages.append(f"replayed ok: volume={result.volume:.3f} mm^3")
    return True, "\n".join(messages), failed_id


def _build(args: argparse.Namespace) -> int:
    success, output, _ = build_document(
        _required_path(args, "document"),
        _path(args, "step"),
        _path(args, "stl"),
        _path(args, "cache_dir"),
        _path(args, "mesh"),
        _path(args, "stl_dir"),
        _path(args, "three_mf"),
    )
    if not success:
        _err(output)
        return 1
    _out(output)
    return 0


def _request_path(request: JSON, key: str) -> Path | None:
    if not isinstance(request, dict):
        raise TypeError("a build request must be a JSON object")
    value = request[key] if key == "document" else request.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key!r} must be a path")
    return Path(value)


def serve() -> int:
    """Answer one JSON build request per line of stdin, keeping build123d warm."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = parse_json(line)
            document = _request_path(request, "document")
            if document is None:
                raise TypeError("'document' must be a path")
            success, output, failed_id = build_document(
                document,
                _request_path(request, "step"),
                _request_path(request, "stl"),
                _request_path(request, "cache_dir"),
                _request_path(request, "mesh"),
            )
        except Exception as exc:
            success, output, failed_id = False, f"error: {exc}", None
        _out(
            json.dumps(
                {"success": success, "output": output, "failed_feature": failed_id}
            )
        )
    return 0


def _daemon(args: argparse.Namespace) -> int:
    # Imported here, not at module scope, so `sprocket build` keeps starting
    # without paying for the socket machinery it never uses.
    from sprocket_engine.rpc import Daemon, Session

    document_path = _required_path(args, "document")
    try:
        document = Document.load(document_path)
    except DocumentError as exc:
        _err(f"error: {exc}")
        return 1

    socket_path = _path(args, "socket") or Path(str(document_path) + ".sock")
    session = Session(document, _path(args, "cache_dir"))
    server = Daemon(
        session,
        socket_path,
        autosave=not _flag(args, "no_autosave"),
        viewer_dir=_path(args, "viewer_dir"),
        recovery_path=_path(args, "recovery"),
    )
    _ = server.publish_viewer_files()
    # A script, `sprocket build`, git or another editor can rewrite the file
    # while the app has it open. See Daemon.check_file for the rule.
    server.start_watching()
    _out(f"listening on {socket_path} (revision {document.revision})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def _schemas() -> int:
    from sprocket_engine.ops import REGISTRY

    _out(
        json.dumps({name: op.schema for name, op in sorted(REGISTRY.items())}, indent=2)
    )
    return 0


def _web(args: argparse.Namespace) -> int:
    from sprocket_engine.rpc import Daemon, Session
    from sprocket_engine.web import WebApp, make_server

    document_path = _required_path(args, "document")
    try:
        document = Document.load(document_path)
    except DocumentError as exc:
        _err(f"error: {exc}")
        return 1

    viewer_dir = (
        _path(args, "viewer_dir") or Path(__file__).resolve().parents[3] / "viewer"
    )
    if not (viewer_dir / "index.html").exists():
        _err(f"error: no viewer at {viewer_dir}: pass --viewer-dir")
        return 1

    socket_path = _path(args, "socket") or Path(str(document_path) + ".sock")
    daemon = Daemon(
        Session(document, _path(args, "cache_dir")),
        socket_path,
        autosave=not _flag(args, "no_autosave"),
    )
    daemon.start_in_background()
    daemon.start_watching()
    server = make_server(WebApp(daemon, viewer_dir), _number(args, "port"))
    _out(
        f"serving http://127.0.0.1:{server.server_address[1]}/ (agents attach at {socket_path})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        daemon.shutdown()
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from sprocket_engine.mcp_server import run

    return run(_path(args, "socket"))


if __name__ == "__main__":
    raise SystemExit(main())
