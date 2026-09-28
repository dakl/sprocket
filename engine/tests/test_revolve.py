"""The full-turn geometry is pinned by tests/golden/revolve_ring. This covers
the two things that case cannot reach: a partial angle, and an axis given as an
explicit line rather than a name.
"""

import math

import pytest

from sprocket_engine.replay import replay

# Spans 10 to 20 mm from the Z axis, 5 mm tall. A full turn gives a
# square-section ring of volume pi * (20^2 - 10^2) * 5.
RING_PROFILE = {"id": "f1", "type": "sketch", "plane": "XZ", "rect": {"width": 10, "height": 5, "center": [15, 2.5]}}
FULL_TURN_VOLUME = math.pi * (20**2 - 10**2) * 5


def document_with(revolve_feature: dict) -> dict:
    return {"schema_version": 1, "units": {"length": "mm"}, "features": [RING_PROFILE, revolve_feature]}


@pytest.mark.parametrize("angle, fraction", [(90, 0.25), (180, 0.5), (270, 0.75)])
def test_angle_sweeps_that_fraction_of_a_turn(angle, fraction):
    feature = {"id": "f2", "type": "revolve", "profile": "f1", "axis": "Z", "angle": angle}
    part = replay(document_with(feature))
    assert part.volume == pytest.approx(FULL_TURN_VOLUME * fraction, rel=1e-4)


def test_angle_defaults_to_a_full_turn():
    feature = {"id": "f2", "type": "revolve", "profile": "f1", "axis": "Z"}
    part = replay(document_with(feature))
    assert part.volume == pytest.approx(FULL_TURN_VOLUME, rel=1e-4)


def test_an_explicit_axis_uses_its_origin_not_just_its_direction():
    """Revolving the same profile about a line through x = 30 mirrors it: the
    profile now spans 10 to 20 mm from that line, so the volume is unchanged
    and only the position moves. A dropped origin would keep the volume right,
    which is why this asserts the bounding box."""
    feature = {
        "id": "f2",
        "type": "revolve",
        "profile": "f1",
        "axis": {"origin": [30, 0, 0], "direction": [0, 0, 1]},
    }
    part = replay(document_with(feature))
    assert part.volume == pytest.approx(FULL_TURN_VOLUME, rel=1e-4)
    bbox = part.bounding_box()
    assert (bbox.min.X, bbox.max.X) == pytest.approx((10, 50), abs=1e-6)


def test_an_angle_over_a_full_turn_is_rejected():
    feature = {"id": "f2", "type": "revolve", "profile": "f1", "axis": "Z", "angle": 540}
    with pytest.raises(Exception):
        replay(document_with(feature))
