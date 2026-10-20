"""Write a golden case's captured files from the current engine.

Three files: expected.step, expected.stl, and captured_with.json recording which
engine made them. The byte-identity layer only runs when that stamp matches the
engine running the tests, so all three are written together or not at all.

Run it through the Makefile:

    make golden-update CASE=circle_extrude               # a new case
    make golden-update CASE=rect_extrude APPROVED=1      # an existing one

These are the only golden files that are captured rather than written by hand,
and that is exactly why an existing case needs approval. Regenerating
one turns a real regression into the new expectation, silently, and the suite
then defends the bug for as long as the case lives. checks.json is never
touched here: those numbers are derived by hand before the code exists, and
that is what makes a case prove correctness rather than sameness.

"Existing" means the case already has expected files. A case that has none has
nothing to lose, so it does not need approval.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from golden_support import (  # noqa: E402
    CAPTURE_STAMP,
    CASES,
    EXPECTED_STEP,
    EXPECTED_STL,
    build,
    case_dir,
    engine_identity,
    write_capture_identity,
    write_step,
    write_stl,
)

# Every file this script writes, so the approval gate and the atomic move stay
# in step with each other. Anything added here is captured, not hand-written.
CAPTURED = (EXPECTED_STEP, EXPECTED_STL, CAPTURE_STAMP)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golden-update", description=__doc__)
    parser.add_argument("case", help=f"one of: {', '.join(CASES)}")
    parser.add_argument(
        "--approved",
        action="store_true",
        help="overwrite a case that already has expected files",
    )
    args = parser.parse_args(argv)

    if args.case not in CASES:
        print(
            f"no such golden case: {args.case}\nknown cases: {', '.join(CASES)}",
            file=sys.stderr,
        )
        return 2

    directory = case_dir(args.case)
    already_there = [name for name in CAPTURED if (directory / name).is_file()]
    if already_there and not args.approved:
        print(
            f"{args.case} already has {', '.join(already_there)}.\n"
            "Regenerating an existing case replaces the expectation with whatever the engine now\n"
            "produces, so a regression would be committed as correct. If the change is intended,\n"
            "re-run with APPROVED=1 and say in the commit message what changed and why.",
            file=sys.stderr,
        )
        return 2

    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        part = build(args.case, scratch / "cache")
        # Exported to scratch first, then moved. A failed export part way through
        # would otherwise leave the case with a fresh STEP and a stale STL, which
        # reads as a geometry change on the next run.
        write_step(part, scratch / EXPECTED_STEP)
        write_stl(part, scratch / EXPECTED_STL)
        # The stamp moves with the bytes it describes. A stamp written separately
        # could name an engine that did not produce the files beside it, and the
        # byte layer would then compare against the wrong expectation or skip when
        # it should have run.
        write_capture_identity(scratch / CAPTURE_STAMP)
        for name in CAPTURED:
            shutil.move(scratch / name, directory / name)

    print(f"wrote {', '.join(CAPTURED)} in {directory}")
    print(f"engine {engine_identity()}")
    print(f"volume {part.volume:.6f} mm^3, {len(part.faces())} faces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
