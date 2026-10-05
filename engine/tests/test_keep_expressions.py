"""A viewer or solver edit writes numbers. The stored "=param" fields it did not change must stay expressions."""

import copy

import pytest

from conftest import running
from sprocket_engine.document import Document, resolve_value
from sprocket_engine.rpc import Client

PARAMETERS = {"pcb_l": 40, "pcb_w": 20, "r": 3}
SKETCH = {
    "id": "s",
    "type": "sketch",
    "plane": "XY",
    "entities": [
        {"id": "e1", "kind": "line", "p0": [0, 0], "p1": ["=pcb_l", 0]},
        {"id": "e2", "kind": "line", "p0": ["=pcb_l", 0], "p1": ["=pcb_l", "=pcb_w"]},
        {"id": "e3", "kind": "line", "p0": ["=pcb_l", "=pcb_w"], "p1": [0, "=pcb_w"]},
        {"id": "e4", "kind": "line", "p0": [0, "=pcb_w"], "p1": [0, 0]},
        {"id": "e5", "kind": "circle", "c": ["=pcb_l/2", "=pcb_w/2"], "r": "=r"},
    ],
    "constraints": [
        {"id": "c1", "kind": "horizontal", "e": "e1"},
        {"id": "c2", "kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": "=pcb_l"},
        {"id": "c3", "kind": "radius", "e": "e5", "value": "=r"},
    ],
}


def viewer_save(document, move_circle_to):
    """What the viewer sends: the whole sketch as numbers, with one point moved."""
    numbers = resolve_value(copy.deepcopy(document.features[0]), document.resolved_parameters())
    numbers["entities"][4]["c"] = move_circle_to
    return numbers


def test_a_viewer_edit_keeps_the_expressions_it_did_not_change():
    document = Document([copy.deepcopy(SKETCH)], parameters=PARAMETERS)
    document.apply_edits([{"op": "update", "feature": viewer_save(document, [25, 10])}], None, "viewer")
    stored = document.features[0]
    assert stored["entities"][0]["p1"] == ["=pcb_l", 0]
    assert stored["entities"][2]["p0"] == ["=pcb_l", "=pcb_w"]
    assert stored["entities"][4]["r"] == "=r"
    assert stored["constraints"][1]["value"] == "=pcb_l"
    # Only the moved coordinate lost its link; its other coordinate kept it.
    assert stored["entities"][4]["c"] == [25, "=pcb_w/2"]
    assert document.log[-1]["unlinked"] == [{"field": "entities[e5].c[0]", "was": "=pcb_l/2", "now": 25}]


def test_the_untouched_fields_still_follow_a_parameter_change():
    document = Document([copy.deepcopy(SKETCH)], parameters=PARAMETERS)
    document.apply_edits([{"op": "update", "feature": viewer_save(document, [25, 10])}], None, "viewer")
    document.apply_edits([{"op": "set_parameter", "name": "pcb_l", "value": 50}], None, "agent")
    resolved = document.for_replay()["features"][0]
    assert resolved["entities"][0]["p1"] == [50, 0]
    assert resolved["entities"][4]["c"] == [25, 10]


def test_the_daemon_reply_names_the_replaced_link(tmp_path):
    with running({"features": [copy.deepcopy(SKETCH)], "parameters": PARAMETERS}, tmp_path) as daemon, Client(daemon.socket_path) as client:
        document = daemon.session.document
        reply = client.call("apply_edit", edits=[{"op": "update", "feature": viewer_save(document, [25, 10])}])
        assert reply["unlinked"][0]["was"] == "=pcb_l/2"
        assert client.call("get_document")["features"][0]["entities"][1]["p1"] == ["=pcb_l", "=pcb_w"]


def test_an_engine_solved_sketch_edit_keeps_expressions_too():
    pytest.importorskip("planegcs")
    document = Document([copy.deepcopy(SKETCH)], parameters=PARAMETERS)
    edit = {"op": "edit_sketch", "id": "s", "action": "update_entity", "entity_id": "e5", "changes": {"r": 4}}
    document.apply_edits([edit], None, "agent")
    stored = document.features[0]
    assert stored["entities"][0]["p1"] == ["=pcb_l", 0]
    assert stored["constraints"][2]["value"] == "=r"
    assert stored["entities"][4]["r"] == "=r"  # the radius constraint solved it back to 3
