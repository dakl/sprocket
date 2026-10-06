"""Projected reference geometry (sketch_reference.py): an edge of an earlier
sketch or body, locked inside another sketch, that a dimension can name.

The checks are written from the geometry, not captured from the engine's own
output: where a projected edge must land follows from the planes, and where the
inner rectangle must sit follows from the dimension.
"""

import copy
import math

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.replay import replay

pytest.importorskip("planegcs")

from sprocket_engine.rpc import Client, RpcError
from sprocket_engine.sketch_reference import project_edge

from conftest import running


def rectangle(left, bottom, right, top, extra=()):
    """A rectangle of four lines, joined, level and upright. e4 is the left edge, drawn downwards."""
    return {
        "entities": [
            {"id": "e1", "kind": "line", "p0": [left, bottom], "p1": [right, bottom]},
            {"id": "e2", "kind": "line", "p0": [right, bottom], "p1": [right, top]},
            {"id": "e3", "kind": "line", "p0": [right, top], "p1": [left, top]},
            {"id": "e4", "kind": "line", "p0": [left, top], "p1": [left, bottom]},
        ],
        "constraints": [
            {"kind": "coincident", "a": "e1.p1", "b": "e2.p0"},
            {"kind": "coincident", "a": "e2.p1", "b": "e3.p0"},
            {"kind": "coincident", "a": "e3.p1", "b": "e4.p0"},
            {"kind": "coincident", "a": "e4.p1", "b": "e1.p0"},
            {"kind": "horizontal", "e": "e1"},
            {"kind": "horizontal", "e": "e3"},
            {"kind": "vertical", "e": "e2"},
            {"kind": "vertical", "e": "e4"},
            *extra,
        ],
    }


# The outer rectangle hangs off the origin by its bottom right corner, so its
# left edge sits at -width. Changing the width moves that edge, which is what a
# dimension to it has to follow.
OUTER = {
    "id": "f1", "type": "sketch", "plane": "XY",
    **rectangle(-100, 0, 0, 60, [
        {"kind": "coincident", "a": "e1.p1", "b": "origin"},
        {"id": "width", "kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 100},
    ]),
}
INNER = {"id": "f2", "type": "sketch", "plane": "XY", **rectangle(-90, 10, -20, 40)}
GAP = {"id": "gap", "kind": "distance_x", "a": "e1.p0", "b": "e4.p1", "value": 10}
LEFT_OF_OUTER = {"sketch": "f1", "entity": "e4"}


def document():
    return Document(copy.deepcopy([OUTER, INNER]), 2, {})


def entity(feature, entity_id):
    return next(item for item in feature["entities"] if item["id"] == entity_id)


def project(document, source=LEFT_OF_OUTER, feature="f2"):
    document.apply_edits([{"op": "edit_sketch", "action": "add_reference", "id": feature, "source": source}], None, "test")
    return document.log[-1]["entity_id"]


def built(document):
    return replay(document.for_replay(), return_context=True)[1]


def test_a_line_is_projected_to_where_the_planes_put_it():
    from build123d import Edge, Plane as B3dPlane

    edge = Edge.make_line((-100, 0, 0), (-100, 60, 0))
    assert project_edge(edge, B3dPlane.XY.offset(5)) == {"kind": "line", "p0": [-100.0, 0.0], "p1": [-100.0, 60.0]}
    # An XZ sketch has u along world X and v along world Z.
    upright = Edge.make_line((10, 0, 0), (10, 0, 8))
    assert project_edge(upright, B3dPlane.XZ) == {"kind": "line", "p0": [10.0, 0.0], "p1": [10.0, 8.0]}


def test_an_edge_that_runs_straight_at_the_plane_has_no_projection():
    from build123d import Edge, Plane as B3dPlane

    with pytest.raises(ValueError, match="projects to a point"):
        project_edge(Edge.make_line((3, 4, 0), (3, 4, 9)), B3dPlane.XY)


def test_a_slanted_line_projects_by_dropping_the_height():
    from build123d import Edge, Plane as B3dPlane

    projected = project_edge(Edge.make_line((0, 0, 0), (30, 40, 7)), B3dPlane.XY)
    assert projected == {"kind": "line", "p0": [0.0, 0.0], "p1": [30.0, 40.0]}


def test_a_circle_projects_to_a_circle_only_onto_a_parallel_plane():
    from build123d import Edge, Plane as B3dPlane

    ring = B3dPlane.XY.offset(3) * Edge.make_circle(7, B3dPlane.XY.offset(0))
    assert project_edge(ring, B3dPlane.XY) == {"kind": "circle", "c": [0.0, 0.0], "r": 7.0}
    with pytest.raises(ValueError, match="parallel"):
        project_edge(ring, B3dPlane.XZ)


def test_a_quarter_arc_keeps_its_ends_and_its_direction_seen_from_below():
    from build123d import Edge, Plane as B3dPlane

    quarter = Edge.make_circle(10, B3dPlane.XY, start_angle=0, end_angle=90)
    up = project_edge(quarter, B3dPlane.XY)
    assert (up["a0"], up["a1"]) == pytest.approx((0, math.pi / 2))
    # The plane facing -Z flips v, so the same arc runs from -90 to 0 degrees there.
    down = project_edge(quarter, B3dPlane(origin=(0, 0, 0), z_dir=(0, 0, -1), x_dir=(1, 0, 0)))
    assert (down["a0"], down["a1"]) == pytest.approx((-math.pi / 2, 0))
    assert down["c"] == [0.0, 0.0] and down["r"] == 10


def test_projecting_adds_a_locked_construction_entity_with_its_source():
    doc = document()
    new_id = project(doc)
    added = entity(doc.features[1], new_id)
    assert new_id == "e5"
    assert added["reference"] == LEFT_OF_OUTER
    assert added["construction"] is True
    assert added["p0"] == [-100, 60] and added["p1"] == [-100, 0]


def test_projecting_the_same_edge_twice_is_refused():
    doc = document()
    project(doc)
    with pytest.raises(DocumentError, match="already projected as 'e5'"):
        project(doc)


def test_a_projected_entity_cannot_be_edited_only_removed():
    doc = document()
    project(doc)
    with pytest.raises(DocumentError, match="locked"):
        doc.apply_edits([{"op": "edit_sketch", "action": "update_entity", "id": "f2", "entity_id": "e5", "changes": {"p0": [0, 0]}}], None, "test")
    doc.apply_edits([{"op": "edit_sketch", "action": "remove_entity", "id": "f2", "entity_id": "e5"}], None, "test")
    assert all(item["id"] != "e5" for item in doc.features[1]["entities"])


def test_the_dimension_to_the_outer_edge_survives_a_change_of_the_outer_width():
    doc = document()
    reference = project(doc)
    doc.apply_edits([{
        "op": "edit_sketch", "action": "add_constraint", "id": "f2",
        "constraint": {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 70},
    }], None, "test")
    # The reference is e5. The gap runs from its lower end to the inner rectangle's.
    doc.apply_edits([{
        "op": "edit_sketch", "action": "add_constraint", "id": "f2",
        "constraint": {"kind": "distance_x", "a": f"{reference}.p1", "b": "e4.p1", "value": 10},
    }], None, "test")
    inner = doc.features[1]
    assert entity(inner, "e4")["p1"][0] == pytest.approx(-90)

    doc.apply_edits([{"op": "edit_sketch", "action": "update_constraint", "id": "f1", "constraint_id": "width", "changes": {"value": 140}}], None, "test")
    assert entity(doc.features[0], "e4")["p0"][0] == pytest.approx(-140)

    # The outer left edge is now at u = -140, so a gap of 10 puts the inner one at -130.
    solved = built(doc).features["f2"]
    assert entity(solved, reference)["p0"][0] == pytest.approx(-140)
    assert entity(solved, "e4")["p0"][0] == pytest.approx(-130)
    assert entity(solved, "e2")["p0"][0] == pytest.approx(-130 + 70)  # the inner rectangle kept its own width
    # The stored copy was brought up to date as well, which is what a viewer reopening the sketch reads.
    doc.sync_references(built(doc).features)
    assert entity(doc.features[1], "e4")["p0"][0] == pytest.approx(-130)


def test_a_reference_is_not_a_region_so_profiles_do_not_change():
    from sprocket_engine.ops.sketch import sketch_profiles

    doc = document()
    before = sketch_profiles(doc.features[1])
    project(doc)
    assert sketch_profiles(doc.features[1]) == before


def test_the_solver_holds_a_reference_still_while_the_rest_is_dragged():
    from sprocket_engine import solver

    doc = document()
    project(doc)
    sketch = {"entities": doc.features[1]["entities"], "constraints": [{"kind": "coincident", "a": "e1.p0", "b": "e5.p1"}]}
    result = solver.solve(sketch)
    assert result["ok"]
    # Joined to the reference's lower end, the inner corner moves to it and the reference stays.
    assert entity(result["sketch"], "e1")["p0"] == pytest.approx([-100, 0])
    assert entity(result["sketch"], "e5")["p1"] == [-100, 0]


def test_distance_y_is_signed_from_a_to_b():
    from sprocket_engine import solver

    sketch = {
        "entities": [{"id": "e1", "kind": "line", "p0": [0, 0], "p1": [5, 5]}, {"id": "e2", "kind": "line", "p0": [1, 1], "p1": [9, 9]}],
        "constraints": [{"kind": "fix", "e": "e1.p0"}, {"kind": "distance_y", "a": "e1.p0", "b": "e2.p1", "value": -4}],
    }
    result = solver.solve(sketch)
    assert result["ok"]
    assert entity(result["sketch"], "e2")["p1"][1] == pytest.approx(-4)


def test_a_source_that_no_longer_exists_names_the_reference():
    doc = document()
    project(doc)
    doc.features[1]["entities"][-1]["reference"] = {"sketch": "f1", "entity": "e9"}
    with pytest.raises(Exception, match="reference 'e5' of sketch 'f2'") as caught:
        replay(doc.for_replay())
    assert caught.value.feature_id == "f2"


def test_the_sketch_a_reference_names_cannot_be_deleted():
    doc = document()
    project(doc)
    with pytest.raises(DocumentError, match="still reference"):
        doc.apply_edits([{"op": "delete", "id": "f1"}], None, "test")


def test_a_body_edge_is_projected_onto_a_sketch_above_it():
    doc = Document(
        [
            {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 30}},
            {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
            {"id": "f3", "type": "sketch", "plane": {"name": "XY", "offset": 10}, **rectangle(-5, -5, 5, 5)},
        ],
        3,
        {},
    )
    # The box spans u -20..20, v -15..15, so its right-hand top edge is the line u = 20.
    edge = {"select": [{"op": "edges"}, {"op": "nearest_to", "point": [20, 0, 10]}]}
    doc.apply_edits([{"op": "edit_sketch", "action": "add_reference", "id": "f3", "source": {"feature": "f2", "edge": edge}}], None, "test")
    added = entity(doc.features[2], "e5")
    assert sorted([added["p0"], added["p1"]]) == [[20, -15], [20, 15]]


def test_the_daemon_projects_for_a_viewer_and_an_agent(tmp_path):
    with running({"features": [OUTER, INNER], "rollback_index": 2}, tmp_path) as daemon, Client(daemon.socket_path) as client:
        shown = client.call("project_reference", plane="XY", source=LEFT_OF_OUTER)
        assert shown["source"] == LEFT_OF_OUTER
        assert shown["entity"] == {"kind": "line", "p0": [-100, 60], "p1": [-100, 0]}
        added = client.call("add_reference", feature="f2", source=LEFT_OF_OUTER)
        assert added["entity_id"] == "e5"
        client.call("add_constraint", feature="f2", constraint={"kind": "distance_x", "a": "e5.p1", "b": "e4.p1", "value": 10})
        client.call("update_constraint", feature="f1", constraint_id="width", changes={"value": 150})
        features = {feature["id"]: feature for feature in client.call("get_document")["features"]}
        assert entity(features["f2"], "e4")["p0"][0] == pytest.approx(-140)
        with pytest.raises(RpcError, match="cannot project"):
            client.call("project_reference", plane="XY", source={"sketch": "f1", "entity": "e99"})


def test_the_agent_tool_adds_a_reference(tmp_path):
    from sprocket_engine import mcp_server

    assert "sketch_add_reference" in mcp_server.SKETCH_TOOL_NAMES
    with running({"features": [OUTER, INNER], "rollback_index": 2}, tmp_path) as daemon, Client(daemon.socket_path) as client:
        reply = mcp_server.call(client, "sketch_add_reference", {"feature": "f2", "source": LEFT_OF_OUTER})
        assert "e5" in reply[0].text


def test_the_viewer_files_draw_a_sketch_as_solved_around_its_references():
    from sprocket_engine.replay import build_shape
    from sprocket_engine.viewer_files import features_summary

    doc = document()
    reference = project(doc)
    doc.apply_edits([{
        "op": "edit_sketch", "action": "add_constraint", "id": "f2",
        "constraint": {"kind": "distance_x", "a": f"{reference}.p1", "b": "e4.p1", "value": 10},
    }], None, "test")
    # A change in f1 that skips the solver, as an `update` from a viewer would: f2 is left stale on purpose.
    doc.features[0]["entities"][3]["p0"] = [-130, 60]
    doc.features[0]["entities"][3]["p1"] = [-130, 0]
    replayable = doc.for_replay()
    _, failed, ctx = build_shape(replayable["features"], replayable["rollback_index"], None)
    assert failed is None
    summary = {entry["id"]: entry for entry in features_summary(doc, failed, ctx)}
    assert entity(summary["f2"]["resolved"], reference)["p0"][0] == pytest.approx(-130)
    assert entity(summary["f2"]["resolved"], "e4")["p0"][0] == pytest.approx(-120)
