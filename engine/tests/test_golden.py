"""Golden feature tests.

Each tests/golden/<case>/ holds a document.json and a checks.json of
independently hand-computed values (see each case's README.md for the
derivation). Replaying document.json must match checks.json within
tolerance. This is the correctness check the spec requires at golden-case
creation time: checks.json is never captured from the engine's own output.
"""

import json
from pathlib import Path

import pytest

from sprocket_engine.replay import replay

GOLDEN_DIR = Path(__file__).parent / "golden"
CASES = sorted(p.name for p in GOLDEN_DIR.iterdir() if p.is_dir())


@pytest.mark.parametrize("case", CASES)
def test_golden_case(case: str, tmp_path: Path):
    case_dir = GOLDEN_DIR / case
    document = json.loads((case_dir / "document.json").read_text())
    checks = json.loads((case_dir / "checks.json").read_text())

    result = replay(document, cache_dir=tmp_path / "cache")
    bbox = result.bounding_box()

    assert result.volume == pytest.approx(checks["volume"], rel=1e-6)
    assert result.area == pytest.approx(checks["surface_area"], rel=1e-6)
    assert list(bbox.min) == pytest.approx(checks["bounding_box"]["min"], abs=1e-6)
    assert list(bbox.max) == pytest.approx(checks["bounding_box"]["max"], abs=1e-6)
    assert list(result.center()) == pytest.approx(checks["center_of_mass"], abs=1e-6)
    assert len(result.faces()) == checks["faces"]
    assert len(result.edges()) == checks["edges"]
    assert len(result.vertices()) == checks["vertices"]


def test_golden_case_replay_is_deterministic(tmp_path: Path):
    document = json.loads((GOLDEN_DIR / "rect_extrude" / "document.json").read_text())
    first = replay(document, cache_dir=tmp_path / "cache")
    second = replay(document, cache_dir=tmp_path / "cache")
    assert first.volume == pytest.approx(second.volume, rel=1e-9)
