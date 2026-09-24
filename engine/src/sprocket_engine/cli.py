"""sprocket build doc.json --step out.step --stl out.stl

This is the M1 agent/human interface: write a document as JSON, run this
command, read the exported files back. No daemon, no MCP server, both of
those wrap this same replay() call later if they turn out to be needed.
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

    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args)
    return 1


def _build(args: argparse.Namespace) -> int:
    document = json.loads(args.document.read_text())
    cache_dir = args.cache_dir or args.document.parent / "cache"

    try:
        result = replay(document, cache_dir=cache_dir)
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.step:
        export_step(result, args.step)
        print(f"wrote {args.step}")
    if args.stl:
        export_stl(result, args.stl)
        print(f"wrote {args.stl}")
    if not args.step and not args.stl:
        print(f"replayed ok: volume={result.volume:.3f} mm^3")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
