import math

import pytest
from build123d import Face, Wire
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sprocket_engine import regions
from sprocket_engine.model import JSONObject
from sprocket_engine.regions import ProfileError, find_regions, pick_region


def rectangle(x0, y0, x1, y1, tag="r", gap=0.0):
    """Four lines. `gap` leaves the corner at (x1, y0) open by that much, as a
    solver that stopped at its tolerance would.
    """
    return [
        {"id": f"{tag}1", "kind": "line", "p0": [x0, y0], "p1": [x1, y0]},
        {"id": f"{tag}2", "kind": "line", "p0": [x1 + gap, y0], "p1": [x1, y1]},
        {"id": f"{tag}3", "kind": "line", "p0": [x1, y1], "p1": [x0, y1]},
        {"id": f"{tag}4", "kind": "line", "p0": [x0, y1], "p1": [x0, y0]},
    ]


def circle(entity_id, cx, cy, radius, **extra):
    return {"id": entity_id, "kind": "circle", "c": [cx, cy], "r": radius, **extra}


def areas(regions):
    return sorted(round(region.area, 3) for region in regions)


def test_a_cell_wound_clockwise_is_turned_to_face_up() -> None:
    """A face built on a clockwise wire faces -Z. Turning it once raised
    AttributeError, because build123d's Wire has no `reversed`."""
    clockwise = Face(
        Wire.make_polygon([(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0)])
    )
    assert clockwise.normal_at().Z < 0

    cleaned = regions._clean(clockwise)

    assert cleaned.normal_at().Z > 0
    assert cleaned.area == pytest.approx(100)


def test_a_rectangle_is_one_region_bounded_by_its_four_sides() -> None:
    (region,) = find_regions(rectangle(0, 0, 40, 20))
    assert region.area == pytest.approx(800)
    assert region.bounded_by == ["r1", "r2", "r3", "r4"]
    assert region.holes == []


def test_a_circle_across_a_side_cuts_the_rectangle_into_three() -> None:
    # Centred on the top side: the circle leaves a half disc outside, a half
    # disc inside, and the rectangle with a bite out of it.
    regions = find_regions(rectangle(0, 0, 40, 20) + [circle("c", 20, 20, 6)])
    half_disc = math.pi * 36 / 2
    assert areas(regions) == pytest.approx(
        sorted([half_disc, half_disc, 800 - half_disc]), abs=1e-3
    )


def test_a_circle_inside_a_rectangle_is_a_disc_and_a_rectangle_with_a_hole() -> None:
    regions = find_regions(rectangle(0, 0, 40, 20) + [circle("c", 20, 10, 4)])
    by_area = sorted(regions, key=lambda region: region.area)
    assert [len(region.holes) for region in by_area] == [0, 1]
    assert by_area[0].area == pytest.approx(math.pi * 16)
    assert by_area[1].area == pytest.approx(800 - math.pi * 16)


def test_a_circle_tangent_to_two_sides_leaves_two_corner_slivers() -> None:
    # Radius 10 at (10, 10) touches x=0 and y=0. Tangency is where a splitter
    # is most likely to drop a cell or invent one.
    regions = find_regions(rectangle(0, 0, 40, 20) + [circle("c", 10, 10, 10)])
    disc = math.pi * 100
    sliver = 100 - math.pi * 25  # a 10 x 10 corner square minus a quarter disc
    assert areas(regions) == pytest.approx(
        sorted([disc, sliver, sliver, 800 - disc - 2 * sliver]), abs=1e-3
    )


def test_a_line_that_ends_inside_a_region_bounds_nothing() -> None:
    regions = find_regions(
        rectangle(0, 0, 40, 20)
        + [{"id": "d", "kind": "line", "p0": [5, 5], "p1": [15, 5]}]
    )
    (region,) = regions
    assert region.bounded_by == ["r1", "r2", "r3", "r4"]
    assert region.holes == []


def test_corners_that_miss_by_a_solver_tolerance_still_close() -> None:
    (region,) = find_regions(rectangle(0, 0, 40, 20, gap=1e-7))
    assert region.area == pytest.approx(800, rel=1e-6)


def toothed_wheel(teeth, tip=15.0):
    """Arcs and lines the way examples/sprocket-icon/gen.py writes them: arc
    centres and line ends rounded to 6 decimals, arc ends left as angles, so
    each corner misses by about 1e-7.
    """
    pitch_radius = 6.35 / (2 * math.sin(math.pi / teeth))
    step = math.tau / teeth

    def at(centre, angle, radius):
        return [
            round(centre[0] + radius * math.cos(angle), 6),
            round(centre[1] + radius * math.sin(angle), 6),
        ]

    arcs = []
    for k in range(teeth):
        angle = k * step
        seat = [
            round(pitch_radius * math.cos(angle), 6),
            round(pitch_radius * math.sin(angle), 6),
        ]
        middle = angle + step / 2
        cap = [
            round((tip - 0.55) * math.cos(middle), 6),
            round((tip - 0.55) * math.sin(middle), 6),
        ]
        arcs.append(
            {
                "id": f"seat{k}",
                "kind": "arc",
                "c": seat,
                "r": 1.7,
                "a0": angle + math.pi / 2 + 0.0873,
                "a1": angle + 1.5 * math.pi - 0.0873,
            }
        )
        arcs.append(
            {
                "id": f"cap{k}",
                "kind": "arc",
                "c": cap,
                "r": 0.55,
                "a0": middle - math.pi / 2,
                "a1": middle + math.pi / 2,
            }
        )
    lines = []
    for k in range(teeth):
        seat, cap, next_seat = arcs[2 * k], arcs[2 * k + 1], arcs[2 * ((k + 1) % teeth)]
        lines.append(
            {
                "id": f"up{k}",
                "kind": "line",
                "p0": at(seat["c"], seat["a0"], 1.7),
                "p1": at(cap["c"], cap["a0"], 0.55),
            }
        )
        lines.append(
            {
                "id": f"dn{k}",
                "kind": "line",
                "p0": at(cap["c"], cap["a1"], 0.55),
                "p1": at(next_seat["c"], next_seat["a1"], 1.7),
            }
        )
    return arcs + lines


def test_the_outline_follows_the_wire_when_corners_miss_by_rounding() -> None:
    """The viewer fills a region from its outline. The outline used to reverse
    an edge whenever its end missed the last point by more than 1e-9, so a
    sketch with rounded coordinates came out as a self-crossing polygon: the
    face was right and the fill on screen was a tangle of triangles. A simple
    polygon has the face's own area, give or take the chords that stand in for
    the arcs.
    """
    (region,) = find_regions(toothed_wheel(8))
    shoelace = (
        sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(
                region.outline, region.outline[1:] + region.outline[:1]
            )
        )
        / 2
    )
    assert shoelace == pytest.approx(region.area, rel=1e-3)


def test_construction_geometry_bounds_no_region() -> None:
    regions = find_regions(
        rectangle(0, 0, 40, 20) + [circle("c", 20, 10, 4, construction=True)]
    )
    assert len(regions) == 1


@pytest.mark.parametrize(
    "start, end, chord",
    [
        (0, math.pi, [[-5, 0], [5, 0]]),
        (3 * math.pi / 2, math.pi / 2, [[0, 5], [0, -5]]),
    ],
    ids=["plain", "wraps past 2 pi"],
)
def test_an_arc_and_the_chord_across_it_bound_a_half_disc(start, end, chord) -> None:
    entities: list[JSONObject] = [
        {"id": "a", "kind": "arc", "c": [0, 0], "r": 5, "a0": start, "a1": end},
        {"id": "l", "kind": "line", "p0": chord[0], "p1": chord[1]},
    ]
    (region,) = find_regions(entities)
    assert region.area == pytest.approx(math.pi * 25 / 2)


def test_open_drawings_and_empty_ones_have_no_regions() -> None:
    assert find_regions([]) == []
    assert find_regions([{"id": "l", "kind": "line", "p0": [0, 0], "p1": [1, 1]}]) == []


def test_two_regions_can_share_a_bounded_by_and_only_inside_tells_them_apart() -> None:
    regions = find_regions(rectangle(0, 0, 40, 20) + [circle("c", 20, 20, 6)])
    twins = [region for region in regions if region.bounded_by == ["c", "r3"]]
    assert len(twins) == 2
    with pytest.raises(ProfileError, match="gives no 'inside' point"):
        _ = pick_region(regions, {"bounded_by": ["c", "r3"]})
    for twin in twins:
        picked = pick_region(
            regions, {"bounded_by": ["c", "r3"], "inside": list(twin.inside)}
        )
        assert picked.inside == twin.inside


def test_a_reference_that_matches_nothing_is_an_error_not_a_guess() -> None:
    regions = find_regions(rectangle(0, 0, 40, 20))
    with pytest.raises(ProfileError, match="matches 0 regions"):
        _ = pick_region(regions, {"bounded_by": ["r1", "r2", "r9"]})


def test_the_bounded_by_order_in_a_reference_does_not_matter() -> None:
    regions = find_regions(rectangle(0, 0, 40, 20))
    assert pick_region(
        regions, {"bounded_by": ["r4", "r3", "r2", "r1"]}
    ).area == pytest.approx(800)


coordinate = st.integers(min_value=-20, max_value=20)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    coordinate,
    coordinate,
    st.integers(1, 30),
    st.integers(1, 30),
    coordinate,
    coordinate,
    st.integers(1, 30),
    st.integers(1, 30),
)
def test_the_regions_of_two_rectangles_add_up_to_their_union(
    ax, ay, aw, ah, bx, by, bw, bh
):
    """Whatever the overlap, and including rectangles that share an edge or a
    corner, the regions tile the union exactly once.
    """
    overlap_w = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    overlap_h = max(0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - overlap_w * overlap_h
    entities = rectangle(ax, ay, ax + aw, ay + ah, "a") + rectangle(
        bx, by, bx + bw, by + bh, "b"
    )
    regions = find_regions(entities)
    assert sum(region.area for region in regions) == pytest.approx(union, rel=1e-6)
