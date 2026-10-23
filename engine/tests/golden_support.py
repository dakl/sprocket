"""Shared by tests/test_golden.py and tools/golden_update.py.

Both sides have to make the bytes the same way. If the test exported an STL one
way and the updater another, the difference would read as a geometry change and
send somebody looking at the wrong thing.
"""

import json
import platform
import re
from collections import Counter
from pathlib import Path

from build123d import export_step, export_stl

from sprocket_engine import ENGINE_VERSIONS
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
CAPTURE_STAMP = "captured_with.json"


def case_dir(case: str) -> Path:
    return GOLDEN_DIR / case


def document(case: str) -> dict:
    return json.loads((case_dir(case) / "document.json").read_text())


def checks(case: str) -> dict:
    return json.loads((case_dir(case) / "checks.json").read_text())


def engine_identity() -> dict:
    """What the expected.* bytes are actually a property of.

    ENGINE_VERSIONS alone is not enough, and CI measured that on 2026-09-28.
    With build123d 0.13.0 and ocp 8.0.1.0.0 pinned identically by uv.lock, five
    of six cases captured on macOS arm64 failed byte identity on Linux x86_64,
    while layers 1 and 2 passed on all six. cadquery-ocp ships one compiled
    OCCT per platform, five wheels for that one version, so a version string
    cannot tell them apart. The platform belongs in the stamp.
    """
    return {
        **ENGINE_VERSIONS,
        "platform": platform.system(),
        "machine": platform.machine(),
    }


def capture_identity(case: str) -> dict | None:
    """The engine that wrote this case's expected.* files, or None if unrecorded."""
    path = case_dir(case) / CAPTURE_STAMP
    return json.loads(path.read_text()) if path.is_file() else None


def write_capture_identity(path: Path) -> None:
    _ = Path(path).write_text(
        json.dumps(engine_identity(), indent=2, sort_keys=True) + "\n"
    )


def build(case: str, cache_dir: Path):
    return replay(document(case), cache_dir=cache_dir)


def write_step(part, path: Path) -> None:
    _ = export_step(part, Path(path))


def write_stl(part, path: Path) -> None:
    _ = export_stl(
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
