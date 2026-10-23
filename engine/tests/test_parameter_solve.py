"""A bare `set_parameter` moves the geometry of the sketches a parameter drives.

Replay does not solve, so before this a dimension written as `"=width"` changed
its own number and nothing else. The numbers below follow from the geometry: a
rectangle `w` wide and 20 tall, extruded 10, has the volume 200 * w.
"""

import copy
import sys

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.replay import replay

pytest.importorskip("planegcs")

from pathlib import Path

from conftest import running
from test_sketch_reference import entity, rectangle

from sprocket_engine.model import JSONObject
from sprocket_engine.rpc import Client


def box_sketch(width="=w", extra=()):
    return {
        "id": "s",
        "type": "sketch",
        "plane": "XY",
        **rectangle(
            0,
            0,
            40,
            20,
            [
                {"kind": "coincident", "a": "e1.p0", "b": "origin"},
                {
                    "id": "width",
                    "kind": "distance",
                    "a": "e1.p0",
                    "b": "e1.p1",
                    "value": width,
                },
                {
                    "id": "height",
                    "kind": "distance",
                    "a": "e2.p0",
                    "b": "e2.p1",
                    "value": 20,
                },
                *extra,
            ],
        ),
    }


EXTRUDE: JSONObject = {"id": "x", "type": "extrude", "profile": "s", "distance": 10}


def volume(document):
    return replay(document.for_replay()).volume


def set_w(document, value):
    document.apply_edits(
        [{"op": "set_parameter", "name": "w", "value": value}], None, "test"
    )
    return document.log[-1]


def test_a_parameter_that_drives_a_dimension_moves_the_sketch_and_the_part() -> None:
    document = Document([box_sketch(), EXTRUDE], 2, {"w": 40})
    assert volume(document) == pytest.approx(8000)
    _ = set_w(document, 60)
    assert volume(document) == pytest.approx(12000)
    sketch = document.features[0]
    assert entity(sketch, "e1")["p1"] == pytest.approx([60, 0])
    assert entity(sketch, "e3")["p0"] == pytest.approx([60, 20])
    # The dimension still reads the parameter.
    assert [
        item["value"]
        for item in sketch["constraints"]
        if isinstance(item.get("value"), str)
    ] == ["=w"]
    # And it follows again, down as well as up.
    _ = set_w(document, 25)
    assert volume(document) == pytest.approx(5000)


def test_an_expression_over_a_parameter_follows_too() -> None:
    document = Document([box_sketch("=2 * half"), EXTRUDE], 2, {"half": 20})
    _ = document.apply_edits(
        [{"op": "set_parameter", "name": "half", "value": 35}], None, "test"
    )
    assert volume(document) == pytest.approx(70 * 20 * 10)


def test_a_parameter_no_sketch_uses_does_not_solve_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sprocket_engine import solver

    def fail(*args, **kwargs):
        raise AssertionError("the solver ran for a parameter that no sketch uses")

    document = Document(
        [box_sketch(40), {**EXTRUDE, "distance": "=depth"}], 2, {"depth": 10}
    )
    monkeypatch.setattr(solver, "solve", fail)
    _ = document.apply_edits(
        [{"op": "set_parameter", "name": "depth", "value": 12}], None, "test"
    )
    assert volume(document) == pytest.approx(40 * 20 * 12)


def test_a_sketch_that_projects_from_the_driven_one_follows_it() -> None:
    """The gap from the outer rectangle's right edge holds, so the inner one moves with the outer width."""
    outer = box_sketch()
    inner: JSONObject = {
        "id": "f2",
        "type": "sketch",
        "plane": "XY",
        **rectangle(45, 5, 55, 15),
    }
    document = Document(copy.deepcopy([outer, inner]), 2, {"w": 40})
    _ = document.apply_edits(
        [
            {
                "op": "edit_sketch",
                "action": "add_reference",
                "id": "f2",
                "source": {"sketch": "s", "entity": "e2"},
            }
        ],
        None,
        "test",
    )
    reference = document.log[-1]["entity_id"]
    _ = document.apply_edits(
        [
            {
                "op": "edit_sketch",
                "action": "add_constraint",
                "id": "f2",
                "constraint": {
                    "kind": "distance_x",
                    "a": f"{reference}.p0",
                    "b": "e4.p1",
                    "value": 5,
                },
            }
        ],
        None,
        "test",
    )
    assert entity(document.features[1], "e4")["p1"][0] == pytest.approx(45)
    _ = set_w(document, 60)
    ctx = replay(document.for_replay(), return_context=True)[1]
    assert entity(ctx.features["f2"], reference)["p0"][0] == pytest.approx(60)
    assert entity(ctx.features["f2"], "e4")["p1"][0] == pytest.approx(65)
    document.sync_references(ctx.features)
    assert entity(document.features[1], "e4")["p1"][0] == pytest.approx(65)


def test_a_value_that_leaves_the_sketch_unsolvable_is_refused_and_changes_nothing() -> (
    None
):
    # A second dimension on the same width, fixed at 40: only w = 40 satisfies both.
    over = box_sketch(
        extra=[
            {"id": "twice", "kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40}
        ]
    )
    document = Document([over, EXTRUDE], 2, {"w": 40})
    with pytest.raises(
        DocumentError, match=r"sketch 's' with no solution.*(width|twice)"
    ):
        _ = document.apply_edits(
            [{"op": "set_parameter", "name": "w", "value": 60}], None, "test"
        )
    assert document.parameters == {"w": 40}
    assert entity(document.features[0], "e1")["p1"] == [40, 0]


def test_without_the_solver_the_reply_names_the_sketches_it_could_not_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sprocket_engine

    monkeypatch.setitem(sys.modules, "sprocket_engine.solver", None)
    monkeypatch.delattr(sprocket_engine, "solver", raising=False)
    document = Document([box_sketch(), EXTRUDE], 2, {"w": 40})
    entry = set_w(document, 60)
    assert entry["unsolved"] == ["s"]
    assert entity(document.features[0], "e1")["p1"] == [40, 0]


def test_the_daemon_moves_the_sketch_and_undo_puts_it_back(tmp_path: Path) -> None:
    with (
        running(
            {
                "features": [box_sketch(), EXTRUDE],
                "rollback_index": 2,
                "parameters": {"w": 40},
            },
            tmp_path,
        ) as daemon,
        Client(daemon.socket_path) as client,
    ):
        assert client.call("measure")["volume"] == pytest.approx(8000)
        _ = client.call("set_parameter", name="w", value=60)
        assert client.call("measure")["volume"] == pytest.approx(12000)
        _ = client.call("undo")
        assert client.call("measure")["volume"] == pytest.approx(8000)
        assert entity(client.call("get_document")["features"][0], "e1")[
            "p1"
        ] == pytest.approx([40, 0])
