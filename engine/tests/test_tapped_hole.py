"""The hole op's `thread`: a tapped hole."""

import math

import pytest
from build123d import Part

from sprocket_engine.document import Document
from sprocket_engine.replay import Context, ReplayError, replay
from sprocket_engine.viewer_files import features_summary

PLATE = [
    {"id": "ps", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 10}},
    {"id": "plate", "type": "extrude", "profile": "ps", "distance": 6},
]


def tapped(**extra: object) -> dict[str, object]:
    return {
        "id": "h",
        "type": "hole",
        "base": "plate",
        "points": [[-5, 0, 6], [5, 0, 6]],
        **extra,
    }


def build(features: list[dict]) -> tuple[Part, Document, Context]:
    document = Document(features)
    part, ctx = replay(document.for_replay(), return_context=True)
    return part, document, ctx


def threads(features: list[dict]) -> list[dict]:
    _, document, ctx = build(features)
    entry = next(item for item in features_summary(document, None, ctx) if item["id"] == "h")
    return entry["threads"]


def test_a_tapped_hole_is_drilled_at_the_tap_drill() -> None:
    part, _, _ = build([*PLATE, tapped(depth=4, thread={"designation": "M3"})])
    # M3 coarse: 3 - 0.5 = 2.5 mm.
    assert part.volume == pytest.approx(20 * 10 * 6 - 2 * math.pi * 1.25**2 * 4)


def test_each_bore_gets_a_cosmetic_thread_from_its_mouth() -> None:
    found = threads([*PLATE, tapped(depth=4, thread={"designation": "M3", "length": 3})])
    assert len(found) == 2
    for thread, x in zip(sorted(found, key=lambda item: item["start"][0]), (-5, 5), strict=True):
        assert thread["internal"] is True
        assert thread["start"] == pytest.approx([x, 0, 6])
        assert thread["direction"] == pytest.approx([0, 0, -1])
        assert thread["length"] == pytest.approx(3)
        assert thread["designation"] == "M3"


def test_a_given_diameter_overrides_the_tap_drill_and_is_still_checked() -> None:
    with pytest.raises(ValueError, match=r"a M3 hole is 2\.375 to 3\.05 mm across"):
        build([*PLATE, tapped(depth=4, diameter=2, thread={"designation": "M3"})])


def test_a_hole_needs_a_diameter_or_a_thread() -> None:
    with pytest.raises(ReplayError, match="failed validation"):
        build([*PLATE, tapped(depth=4)])


def test_a_modeled_tapped_through_hole_is_one_valid_solid() -> None:
    pytest.importorskip("bd_warehouse")
    part, _, _ = build(
        [*PLATE, tapped(through=True, thread={"designation": "M4", "modeled": True})]
    )
    assert part.is_valid
    assert len(part.solids()) == 1
    nominal = 20 * 10 * 6 - 2 * math.pi * 2**2 * 6
    drilled = 20 * 10 * 6 - 2 * math.pi * 1.65**2 * 6
    assert nominal < part.volume < drilled
