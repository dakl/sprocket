import pytest

from sprocket_engine.replay import replay


def test_polygon_on_principal_plane():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [
            {"id": "f1", "type": "sketch", "plane": "XY", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
            {"id": "f2", "type": "extrude", "profile": "f1", "distance": 5},
        ],
    }
    part = replay(document)
    assert part.volume == pytest.approx(10 * 10 * 5, rel=1e-6)


def test_polygon_on_arbitrary_plane_places_geometry_at_the_given_origin():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [
            {
                "id": "f1",
                "type": "sketch",
                "plane": {"origin": [0, 0, 20], "normal": [0, 0, 1], "x_dir": [1, 0, 0]},
                "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
            },
            {"id": "f2", "type": "extrude", "profile": "f1", "distance": 5},
        ],
    }
    part = replay(document)
    assert part.volume == pytest.approx(10 * 10 * 5, rel=1e-6)
    bbox = part.bounding_box()
    assert bbox.min.Z == pytest.approx(20, abs=1e-6)


@pytest.mark.parametrize(
    "winding, polygon",
    [
        ("counter-clockwise", [[0, 0], [10, 0], [10, 10], [0, 10]]),
        ("clockwise", [[0, 10], [10, 10], [10, 0], [0, 0]]),
    ],
)
def test_positive_distance_extrudes_along_the_plane_normal(winding, polygon):
    """The sketcher emits whichever winding the user drew, and the viewer
    measures its drag against the plane normal, so both must extrude up."""
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [
            {"id": "f1", "type": "sketch", "plane": "XY", "polygon": polygon},
            {"id": "f2", "type": "extrude", "profile": "f1", "distance": 5},
        ],
    }
    bbox = replay(document).bounding_box()
    assert (bbox.min.Z, bbox.max.Z) == pytest.approx((0, 5), abs=1e-6), winding


def test_rect_still_works_alongside_polygon_support():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [
            {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 20, "center": [0, 0]}},
            {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
        ],
    }
    part = replay(document)
    assert part.volume == pytest.approx(40 * 20 * 10, rel=1e-6)


def test_sketch_needs_exactly_one_of_rect_or_polygon():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [{"id": "f1", "type": "sketch", "plane": "XY"}],
    }
    with pytest.raises(Exception):
        replay(document)
