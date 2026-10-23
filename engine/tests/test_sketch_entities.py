import math
from pathlib import Path

import pytest

from sprocket_engine.model import JSONObject
from sprocket_engine.regions import ProfileError
from sprocket_engine.replay import failed_feature, replay

RECTANGLE: list[JSONObject] = [
    {"id": "e1", "kind": "line", "p0": [0, 0], "p1": [40, 0]},
    {"id": "e2", "kind": "line", "p0": [40, 0], "p1": [40, 20]},
    {"id": "e3", "kind": "line", "p0": [40, 20], "p1": [0, 20]},
    {"id": "e4", "kind": "line", "p0": [0, 20], "p1": [0, 0]},
]
RING_AND_DISC = RECTANGLE + [{"id": "e5", "kind": "circle", "c": [20, 10], "r": 4}]
DISC_AREA = math.pi * 16


def sketch(entities, constraints=None, plane="XY"):
    feature: JSONObject = {
        "id": "f1",
        "type": "sketch",
        "plane": plane,
        "entities": entities,
    }
    if constraints is not None:
        feature["constraints"] = constraints
    return feature


def extrude(profiles, distance=10, **extra):
    return {
        "id": "f2",
        "type": "extrude",
        "profiles": profiles,
        "distance": distance,
        **extra,
    }


def build(*features, cache_dir=None):
    return replay({"features": list(features)}, cache_dir=cache_dir)


def test_extruding_a_whole_entity_sketch_gives_the_union_of_what_was_drawn() -> None:
    part = build(
        sketch(RING_AND_DISC),
        {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
    )
    assert part.volume == pytest.approx(800 * 10)


def test_a_picked_region_extrudes_alone() -> None:
    disc = extrude([{"sketch": "f1", "bounded_by": ["e5"]}])
    ring = extrude([{"sketch": "f1", "bounded_by": ["e1", "e2", "e3", "e4", "e5"]}])
    assert build(sketch(RING_AND_DISC), disc).volume == pytest.approx(DISC_AREA * 10)
    assert build(sketch(RING_AND_DISC), ring).volume == pytest.approx(
        (800 - DISC_AREA) * 10
    )


def test_several_picked_regions_extrude_together() -> None:
    both = extrude(
        [
            {"sketch": "f1", "bounded_by": ["e5"]},
            {"sketch": "f1", "bounded_by": ["e1", "e2", "e3", "e4", "e5"]},
        ]
    )
    assert build(sketch(RING_AND_DISC), both).volume == pytest.approx(800 * 10)


def test_a_region_on_another_plane_lands_on_that_plane() -> None:
    # XZ has its normal along -Y, so a positive extrude runs towards -Y.
    part = build(
        sketch(RECTANGLE, plane="XZ"),
        extrude([{"sketch": "f1", "bounded_by": ["e1", "e2", "e3", "e4"]}]),
    )
    box = part.bounding_box()
    assert pytest.approx((0, 40), abs=1e-6) == (box.min.X, box.max.X)
    assert pytest.approx((0, 20), abs=1e-6) == (box.min.Z, box.max.Z)
    assert pytest.approx((-10, 0), abs=1e-6) == (box.min.Y, box.max.Y)


def test_a_profile_still_resolves_after_a_dimension_changes() -> None:
    """The reason profiles name entities and not positions: move the circle and
    the same reference finds the same region.
    """
    disc = extrude([{"sketch": "f1", "bounded_by": ["e5"]}])
    bigger = RECTANGLE + [{"id": "e5", "kind": "circle", "c": [25, 10], "r": 6}]
    assert build(sketch(bigger), disc).volume == pytest.approx(math.pi * 36 * 10)


def test_a_profile_that_no_longer_exists_names_the_extrude_that_broke() -> None:
    gone = extrude([{"sketch": "f1", "bounded_by": ["e5"]}])
    with pytest.raises(ProfileError, match="matches 0 regions") as raised:
        _ = build(sketch(RECTANGLE), gone)
    failed = failed_feature(raised.value)
    assert failed is not None
    assert failed.feature_id == "f2"


def test_two_regions_with_one_bounded_by_are_told_apart_by_inside() -> None:
    entities = RECTANGLE + [{"id": "e5", "kind": "circle", "c": [20, 20], "r": 6}]
    outside_half = extrude(
        [{"sketch": "f1", "bounded_by": ["e3", "e5"], "inside": [20, 24]}]
    )
    inside_half = extrude(
        [{"sketch": "f1", "bounded_by": ["e3", "e5"], "inside": [20, 16]}]
    )
    half = math.pi * 36 / 2 * 10
    assert build(sketch(entities), outside_half).volume == pytest.approx(half)
    assert build(sketch(entities), inside_half).volume == pytest.approx(half)
    assert (
        build(sketch(entities), outside_half).bounding_box().center().Y
        > 20
        > build(sketch(entities), inside_half).bounding_box().center().Y
    )


def test_a_sketch_of_one_shape_takes_a_profile_with_no_boundary_ids() -> None:
    rect: JSONObject = {
        "id": "f1",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 10, "height": 10},
    }
    assert build(
        rect, extrude([{"sketch": "f1", "bounded_by": []}])
    ).volume == pytest.approx(1000)


def test_a_profile_that_names_a_missing_sketch_is_an_error() -> None:
    with pytest.raises(ProfileError, match="not a sketch"):
        _ = build(sketch(RECTANGLE), extrude([{"sketch": "nope", "bounded_by": []}]))


def test_an_extrude_has_a_profile_or_profiles_but_not_both() -> None:
    import jsonschema

    from sprocket_engine.ops import REGISTRY

    schema = REGISTRY["extrude"].schema
    jsonschema.validate(
        {"id": "f2", "type": "extrude", "profile": "f1", "distance": 1}, schema
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"id": "f2", "type": "extrude", "distance": 1}, schema)
    both: JSONObject = {
        "id": "f2",
        "type": "extrude",
        "profile": "f1",
        "profiles": [{"sketch": "f1", "bounded_by": []}],
        "distance": 1,
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(both, schema)


def test_an_entity_sketch_survives_the_brep_cache(tmp_path: Path) -> None:
    # Once with the extrude built fresh, once with every feature read back.
    document = (
        sketch(RING_AND_DISC),
        extrude([{"sketch": "f1", "bounded_by": ["e5"]}]),
    )
    first = build(*document, cache_dir=tmp_path)
    second = build(*document, cache_dir=tmp_path)
    assert second.volume == pytest.approx(first.volume)


def test_a_sketch_with_nothing_closed_replays_to_an_empty_shape(tmp_path: Path) -> None:
    open_line = sketch([{"id": "e1", "kind": "line", "p0": [0, 0], "p1": [5, 5]}])
    assert build(open_line, cache_dir=tmp_path).volume == 0
    assert build(open_line, cache_dir=tmp_path).volume == 0


@pytest.mark.parametrize(
    "entities, constraints, message",
    [
        (RECTANGLE + [dict(RECTANGLE[0])], [], "two entities with id 'e1'"),
        (
            RECTANGLE,
            [{"kind": "coincident", "a": "e1.p1", "b": "e9.p0"}],
            "no entity 'e9'",
        ),
        (
            RECTANGLE,
            [{"kind": "coincident", "a": "e1.c", "b": "e2.p0"}],
            "a line has no point 'c'",
        ),
        (RECTANGLE, [{"kind": "distance", "a": "e1.p0", "b": "e1.p1"}], "needs value"),
        (RECTANGLE, [{"kind": "horizontal"}], "needs 'e'"),
        ([{"id": "e1", "kind": "arc", "c": [0, 0], "r": 1}], [], "needs a0, a1"),
    ],
)
def test_a_sketch_that_contradicts_itself_is_refused_with_the_reason(
    entities, constraints, message
):
    with pytest.raises(ValueError, match=message):
        _ = build(sketch(entities, constraints))


def test_constraints_that_name_real_things_are_accepted_and_change_nothing() -> None:
    constraints: list[JSONObject] = [
        {"kind": "horizontal", "e": "e1"},
        {"kind": "coincident", "a": "e1.p1", "b": "e2.p0"},
        {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40},
        {"kind": "radius", "e": "e5", "value": 4},
        {"kind": "tangent", "a": "e5", "b": "e1"},
    ]
    with_constraints = build(
        sketch(RING_AND_DISC, constraints),
        {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
    )
    assert with_constraints.volume == pytest.approx(800 * 10)


def test_point_on_and_the_origin_are_valid_references() -> None:
    constraints: list[JSONObject] = [
        {"kind": "point_on", "a": "e5.c", "b": "e1"},
        {"kind": "coincident", "a": "e1.p0", "b": "origin"},
        {"kind": "distance", "a": "origin", "b": "e5.c", "value": 22},
        {"kind": "point_on", "a": "e5.c", "b": "axis_u"},
        {"kind": "point_on", "a": "e1.p1", "b": "axis_v"},
    ]
    part = build(
        sketch(RING_AND_DISC, constraints),
        {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
    )
    assert part.volume == pytest.approx(800 * 10)


def test_a_misspelled_origin_is_refused() -> None:
    with pytest.raises(ValueError, match="no entity 'orign'"):
        _ = build(
            sketch(RING_AND_DISC, [{"kind": "coincident", "a": "e1.p0", "b": "orign"}])
        )


def test_extruding_a_sketch_with_nothing_closed_says_so() -> None:
    open_line = sketch([{"id": "e1", "kind": "line", "p0": [0, 0], "p1": [5, 5]}])
    with pytest.raises(ProfileError, match="no closed region"):
        _ = build(
            open_line, {"id": "f2", "type": "extrude", "profile": "f1", "distance": 3}
        )
