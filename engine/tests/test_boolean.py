"""tests/golden/boolean_notch pins subtract. This covers the other two
operations and the empty-result guard, which no golden case can hold because a
golden case has to produce a solid to measure.

All four use the same 40 x 20 x 10 box and a second box placed to overlap it
over x 10..20, y -5..5, z 0..10, so the shared volume is 1000 mm3.
"""

import pytest

from sprocket_engine.model import JSONObject
from sprocket_engine.replay import replay

BASE_BOX: list[JSONObject] = [
    {
        "id": "f1",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 40, "height": 20, "center": [0, 0]},
    },
    {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
]


def document_with(operation: str, tool_center, tool_size=(20, 10)) -> dict:
    width, height = tool_size
    return {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [
            *BASE_BOX,
            {
                "id": "f3",
                "type": "sketch",
                "plane": "XY",
                "rect": {"width": width, "height": height, "center": tool_center},
            },
            {"id": "f4", "type": "extrude", "profile": "f3", "distance": 10},
            {
                "id": "f5",
                "type": "boolean",
                "operation": operation,
                "base": "f2",
                "tool": "f4",
            },
        ],
    }


@pytest.mark.parametrize(
    "operation, volume",
    [
        # base 8000, tool 2000, shared 1000
        ("union", 9000),
        ("subtract", 7000),
        ("intersect", 1000),
    ],
)
def test_each_operation_keeps_the_volume_it_should(operation, volume) -> None:
    part = replay(document_with(operation, tool_center=[20, 0]))
    assert part.volume == pytest.approx(volume, rel=1e-9)


def test_an_intersect_of_bodies_that_never_touch_fails() -> None:
    """Carrying an empty solid forward would fail several features later and
    point at the wrong one.
    """
    with pytest.raises(ValueError, match="empty solid"):
        _ = replay(document_with("intersect", tool_center=[40, 0]))


def test_a_subtract_whose_tool_swallows_the_base_fails() -> None:
    document = document_with("subtract", tool_center=[0, 0], tool_size=(60, 40))
    with pytest.raises(ValueError, match="empty solid"):
        _ = replay(document)
