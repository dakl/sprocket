import json
from pathlib import Path

import pytest

from sprocket_engine.replay import replay
from sprocket_engine.selectors import SelectorError

RECT_EXTRUDE = json.loads(
    (Path(__file__).parent / "golden" / "rect_extrude" / "document.json").read_text()
)


def _with_push(distance, point=(0, 0, 10), normal=(0, 0, 1)):
    document = json.loads(json.dumps(RECT_EXTRUDE))
    document["features"].append(
        {
            "id": "f3",
            "type": "extrude_face",
            "base": "f2",
            "face": {
                "from": "part",
                "select": [
                    {"op": "faces"},
                    {"op": "nearest_to", "point": list(point), "normal": list(normal)},
                ],
            },
            "distance": distance,
        }
    )
    return document


def test_push_pull_adds_material_on_the_picked_face(tmp_path: Path):
    part = replay(_with_push(5), cache_dir=tmp_path / "cache")
    assert part.volume == pytest.approx(8000 + 40 * 20 * 5, rel=1e-6)


def test_push_pull_picks_the_face_nearest_the_click_point(tmp_path: Path):
    # click near a corner of the top face, not its centre - nearest_to must
    # still land on the top face, not one of the four side faces.
    part = replay(_with_push(5, point=(19, 9, 10)), cache_dir=tmp_path / "cache")
    assert part.volume == pytest.approx(8000 + 40 * 20 * 5, rel=1e-6)


def test_push_pull_cuts_material_when_pushed_inward(tmp_path: Path):
    part = replay(_with_push(-3), cache_dir=tmp_path / "cache")
    assert part.volume == pytest.approx(8000 - 40 * 20 * 3, rel=1e-6)


def test_push_pull_off_a_bare_sketch_extrudes_instead_of_fusing(tmp_path: Path):
    # base is a flat sketch here, not a solid yet, so there's nothing to
    # fuse/cut against - this used to raise "Null TopoDS_Shape object".
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [
            {
                "id": "f1",
                "type": "sketch",
                "plane": "XY",
                "rect": {"width": 40, "height": 20, "center": [0, 0]},
            },
            {
                "id": "f2",
                "type": "extrude_face",
                "base": "f1",
                "face": {
                    "from": "part",
                    "select": [
                        {"op": "faces"},
                        {"op": "nearest_to", "point": [0, 0, 0], "normal": [0, 0, 1]},
                    ],
                },
                "distance": 10,
            },
        ],
    }
    part = replay(document, cache_dir=tmp_path / "cache")
    assert part.volume == pytest.approx(40 * 20 * 10, rel=1e-6)


def test_push_pull_rejects_a_selector_matching_more_than_one_face(tmp_path: Path):
    document = json.loads(json.dumps(RECT_EXTRUDE))
    document["features"].append(
        {
            "id": "f3",
            "type": "extrude_face",
            "base": "f2",
            "face": {
                "from": "part",
                "select": [{"op": "faces"}],
            },  # all 6 faces, no narrowing
            "distance": 5,
        }
    )
    with pytest.raises(SelectorError, match="matched 6 faces"):
        replay(document, cache_dir=tmp_path / "cache")
