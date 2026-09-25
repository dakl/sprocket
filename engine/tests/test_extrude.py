import pytest

from sprocket_engine.replay import replay

SKETCH = {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 20, "center": [0, 0]}}


def test_negative_distance_flips_direction_instead_of_failing_validation():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [SKETCH, {"id": "f2", "type": "extrude", "profile": "f1", "distance": -10}],
    }
    part = replay(document)
    assert part.volume == pytest.approx(40 * 20 * 10, rel=1e-6)
