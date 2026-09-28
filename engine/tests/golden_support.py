"""Shared by tests/test_golden.py and tools/golden_update.py.

Both sides have to make the bytes the same way. If the test exported an STL one
way and the updater another, the difference would read as a geometry change and
send somebody looking at the wrong thing.
"""

import json
import re
from collections import Counter
from pathlib import Path

from build123d import export_step, export_stl

from sprocket_engine.replay import replay

GOLDEN_DIR = Path(__file__).parent / "golden"
CASES = sorted(path.name for path in GOLDEN_DIR.iterdir() if path.is_dir())

# Measured, not assumed. Two STEP exports of the same solid ten hours apart
# differed on exactly one of 803 lines, the FILE_NAME timestamp, and every
# float matched byte for byte. So one substitution is the whole of STEP
# normalisation, and no STEP parser is needed.
STEP_TIMESTAMP = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# Pinned rather than left to build123d's defaults, so a changed hash answers
# "did the geometry change" and not "did a library default change".
STL_TOLERANCE = 1e-3
STL_ANGULAR_TOLERANCE = 0.1

EXPECTED_STEP = "expected.step"
EXPECTED_STL = "expected.stl"


def case_dir(case: str) -> Path:
    return GOLDEN_DIR / case


def document(case: str) -> dict:
    return json.loads((case_dir(case) / "document.json").read_text())


def checks(case: str) -> dict:
    return json.loads((case_dir(case) / "checks.json").read_text())


def build(case: str, cache_dir: Path):
    return replay(document(case), cache_dir=cache_dir)


def write_step(part, path: Path) -> None:
    export_step(part, Path(path))


def write_stl(part, path: Path) -> None:
    export_stl(
        part,
        Path(path),
        tolerance=STL_TOLERANCE,
        angular_tolerance=STL_ANGULAR_TOLERANCE,
        ascii_format=False,
    )


def normalised_step(path: Path) -> bytes:
    return STEP_TIMESTAMP.sub(b"<timestamp>", Path(path).read_bytes())


def face_type_histogram(part) -> dict[str, int]:
    """Counts faces by surface kind: PLANE, CYLINDER, BSPLINE_SURFACE and so on.

    This is the only fingerprint entry that separates a fillet from a chamfer of
    the same size. Both give 10 faces, 24 edges and 16 vertices on the same box,
    so without it an op wired to the other one's code passes both cases.
    """
    return dict(sorted(Counter(face.geom_type.name for face in part.faces()).items()))
