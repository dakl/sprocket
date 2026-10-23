"""Edits inside one sketch (sketch_edit.py), through the daemon and the MCP adapter's `call`.

They need the solver, so they skip without the `solver` extra. `make test-solver`
runs them.
"""

import json
import math

import pytest

pytest.importorskip("planegcs")

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.rpc import RpcError

from conftest import running
from solver_cases import rough_rectangle

SKETCH = {"id": "s1", "type": "sketch", "plane": "XY", **rough_rectangle()}
DOCUMENT = {"features": [SKETCH], "rollback_index": 1}
LENGTH = {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40}


@pytest.fixture
def sketch_client(tmp_path):
    from sprocket_engine.rpc import Client

    with running(DOCUMENT, tmp_path) as daemon, Client(daemon.socket_path) as client:
        yield client


def length(sketch, entity_id):
    entity = next(item for item in sketch["entities"] if item["id"] == entity_id)
    return math.dist(entity["p0"], entity["p1"])


def test_constraints_without_ids_get_them_when_the_document_loads(tmp_path):
    path = tmp_path / "old.sprocket"
    path.write_text(json.dumps(DOCUMENT))
    constraints = Document.load(path).features[0]["constraints"]
    assert [constraint["id"] for constraint in constraints] == [
        f"c{n}" for n in range(1, 9)
    ]


def test_a_dimension_moves_the_geometry_and_takes_a_degree_of_freedom(sketch_client):
    before = sketch_client.call(
        "add_constraint", feature="s1", constraint={"kind": "vertical", "e": "e2"}
    )
    result = sketch_client.call("add_constraint", feature="s1", constraint=LENGTH)
    assert result["constraint_id"] == "c10"
    assert length(result["sketch"], "e1") == pytest.approx(40)
    assert result["dof"] == before["dof"] - 1


def test_an_edit_that_has_no_solution_is_refused_and_names_the_conflict(sketch_client):
    sketch_client.call("add_constraint", feature="s1", constraint=LENGTH)
    revision = sketch_client.call("get_document")["revision"]
    with pytest.raises(RpcError) as caught:
        sketch_client.call(
            "add_constraint", feature="s1", constraint={**LENGTH, "value": 55}
        )
    assert "c9" in caught.value.args[0] and "c10" in caught.value.args[0]
    assert sketch_client.call("get_document")["revision"] == revision


def test_updating_a_dimension_by_id_moves_the_geometry(sketch_client):
    added = sketch_client.call("add_constraint", feature="s1", constraint=LENGTH)
    result = sketch_client.call(
        "update_constraint",
        feature="s1",
        constraint_id=added["constraint_id"],
        changes={"value": 25},
    )
    assert length(result["sketch"], "e1") == pytest.approx(25)


def test_an_id_still_means_the_same_constraint_after_another_is_removed(sketch_client):
    added = sketch_client.call("add_constraint", feature="s1", constraint=LENGTH)
    sketch_client.call("remove_constraint", feature="s1", constraint_id="c1")
    result = sketch_client.call(
        "update_constraint",
        feature="s1",
        constraint_id=added["constraint_id"],
        changes={"value": 25},
    )
    assert length(result["sketch"], "e1") == pytest.approx(25)


def test_removing_a_constraint_frees_the_geometry(sketch_client):
    added = sketch_client.call("add_constraint", feature="s1", constraint=LENGTH)
    removed = sketch_client.call(
        "remove_constraint", feature="s1", constraint_id=added["constraint_id"]
    )
    assert removed["dof"] == added["dof"] + 1
    assert all(
        constraint["id"] != added["constraint_id"]
        for constraint in removed["sketch"]["constraints"]
    )


def test_removing_an_entity_removes_the_constraints_that_mention_it(sketch_client):
    result = sketch_client.call("remove_entity", feature="s1", entity_id="e4")
    assert [entity["id"] for entity in result["sketch"]["entities"]] == [
        "e1",
        "e2",
        "e3",
    ]
    assert not any(
        "e4" in str(constraint) for constraint in result["sketch"]["constraints"]
    )


def test_an_added_entity_is_numbered_after_the_highest(sketch_client):
    result = sketch_client.call(
        "add_entity", feature="s1", entity={"kind": "circle", "c": [10, 10], "r": 2}
    )
    assert result["entity_id"] == "e5"


def test_moving_an_entity_keeps_what_the_constraints_hold(sketch_client):
    result = sketch_client.call(
        "update_entity", feature="s1", entity_id="e1", changes={"p1": [38, 9]}
    )
    first = next(item for item in result["sketch"]["entities"] if item["id"] == "e1")
    assert first["p0"][1] == pytest.approx(first["p1"][1])


def test_a_dimension_may_be_a_parameter_and_the_stored_constraint_keeps_the_expression(
    tmp_path,
):
    document = Document([dict(SKETCH)], 1, {"width": 30})
    document.apply_edits(
        [
            {
                "op": "edit_sketch",
                "id": "s1",
                "action": "add_constraint",
                "constraint": {**LENGTH, "value": "=width"},
            }
        ],
        None,
        "test",
    )
    stored = document.features[0]
    assert stored["constraints"][-1]["value"] == "=width"
    assert length(stored, "e1") == pytest.approx(30)


def test_a_sketch_edit_on_something_else_is_refused():
    document = Document(
        [
            {
                "id": "s1",
                "type": "sketch",
                "plane": "XY",
                "rect": {"width": 4, "height": 4},
            }
        ],
        1,
    )
    with pytest.raises(DocumentError, match="not a sketch of lines"):
        document.apply_edits(
            [
                {
                    "op": "edit_sketch",
                    "id": "s1",
                    "action": "remove_entity",
                    "entity_id": "e1",
                }
            ],
            None,
            "test",
        )


def test_the_mcp_tools_reach_the_daemon(sketch_client):
    pytest.importorskip("mcp")
    from sprocket_engine.mcp_server import call, sketch_tools

    assert {tool.name for tool in sketch_tools()} >= {
        "sketch_add_constraint",
        "sketch_add_entity",
    }
    result = call(
        sketch_client, "sketch_add_constraint", {"feature": "s1", "constraint": LENGTH}
    )
    assert "c9" in result[0].text


def test_symmetric_puts_two_points_at_mirror_images_across_a_line(tmp_path):
    from sprocket_engine.rpc import Client

    sketch = {
        "id": "s1",
        "type": "sketch",
        "plane": "XY",
        "entities": [
            {"id": "e1", "kind": "line", "p0": [0, -20], "p1": [0, 20]},
            {"id": "e2", "kind": "line", "p0": [5, 0], "p1": [15, 10]},
            {"id": "e3", "kind": "line", "p0": [-7, 3], "p1": [-12, 9]},
            {"id": "e4", "kind": "circle", "c": [30, 30], "r": 2},
        ],
        "constraints": [{"kind": "fix", "e": "e1"}, {"kind": "fix", "e": "e2"}],
    }
    with (
        running({"features": [sketch], "rollback_index": 1}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        client.call(
            "add_constraint",
            feature="s1",
            constraint={"kind": "symmetric", "a": "e2.p0", "b": "e3.p0", "e": "e1"},
        )
        result = client.call(
            "add_constraint",
            feature="s1",
            constraint={"kind": "symmetric", "a": "e2.p1", "b": "e3.p1", "e": "e1"},
        )
        copy = next(item for item in result["sketch"]["entities"] if item["id"] == "e3")
        # The line u = 0 negates u, so (5, 0) and (15, 10) land at (-5, 0) and (-15, 10).
        assert copy["p0"] == pytest.approx([-5, 0])
        assert copy["p1"] == pytest.approx([-15, 10])
        # The mirror needs a line: a circle is refused with a reason.
        with pytest.raises(RpcError, match="expected line"):
            client.call(
                "add_constraint",
                feature="s1",
                constraint={"kind": "symmetric", "a": "e2.p0", "b": "e3.p0", "e": "e4"},
            )
