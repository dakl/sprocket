"""sprocket build doc.json --step out.step --stl out.stl
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


def _build_document(
    document_path: Path, step: Path | None, stl: Path | None, cache_dir: Path | None
) -> tuple[bool, str]:
    """Replays one document and exports it. Shared by `build` and `serve`
    so the daemon path can't silently drift from the one-shot CLI path."""
    document = json.loads(Path(document_path).read_text())
    resolved_cache_dir = Path(cache_dir) if cache_dir else Path(document_path).parent / "cache"

    try:
        result = replay(document, cache_dir=resolved_cache_dir)
    except ReplayError as exc:
        return False, f"error: {exc}"

    messages = []
    if step:
        export_step(result, Path(step))
        messages.append(f"wrote {step}")
    if stl:
        export_stl(result, Path(stl))
        messages.append(f"wrote {stl}")
    if not step and not stl:
        messages.append(f"replayed ok: volume={result.volume:.3f} mm^3")
    return True, "\n".join(messages)


def _build(args: argparse.Namespace) -> int:
    success, output = _build_document(args.document, args.step, args.stl, args.cache_dir)
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
            success, output = _build_document(
                request["document"], request.get("step"), request.get("stl"), request.get("cache_dir")
            )
        except Exception as exc:
            success, output = False, f"error: {exc}"
        print(json.dumps({"success": success, "output": output}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
