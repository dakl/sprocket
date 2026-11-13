"""The split op. Keeping both sides is the golden case split_box_both."""

import pytest

from sprocket_engine.document import Document
from sprocket_engine.replay import replay

BOX = [
    {"id": "bs", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 10}},
    {"id": "box", "type": "extrude", "profile": "bs", "distance": 10},
]


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


@pytest.mark.parametrize(
    ("keep", "volume", "bottom", "top"), [("above", 1200, 4, 10), ("below", 800, 0, 4)]
)
def test_keep_one_side_of_the_plane(keep, volume, bottom, top):
    part, ctx = build(
        BOX
        + [
            {
                "id": "s",
                "type": "split",
                "base": "box",
                "plane": {"name": "XY", "offset": 4},
                "keep": keep,
            }
        ]
    )
    assert part.volume == pytest.approx(volume)
    box = part.bounding_box()
    assert (box.min.Z, box.max.Z) == pytest.approx((bottom, top))
    assert [body["id"] for body in ctx.bodies] == ["box"], (
        "a split continues its base's body"
    )


def test_both_halves_have_their_own_cut_face():
    part, _ = build(
        BOX
        + [
            {
                "id": "s",
                "type": "split",
                "base": "box",
                "plane": {"name": "XY", "offset": 4},
            }
        ]
    )
    first, second = part.solids()
    assert len(first.faces()) == len(second.faces()) == 6
    assert not any(a.is_same(b) for a in first.faces() for b in second.faces())


def test_a_plane_that_misses_the_body_is_refused():
    with pytest.raises(ValueError, match="does not cross 'box'"):
        build(
            BOX
            + [
                {
                    "id": "s",
                    "type": "split",
                    "base": "box",
                    "plane": {"name": "XY", "offset": 40},
                }
            ]
        )
    with pytest.raises(ValueError, match="kept nothing"):
        build(
            BOX
            + [
                {
                    "id": "s",
                    "type": "split",
                    "base": "box",
                    "plane": {"name": "XY", "offset": 40},
                    "keep": "above",
                }
            ]
        )
