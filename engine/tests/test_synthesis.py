"""Pick -> selector (M4): the selector a pick turns into has to outlive the edit that renumbers the part."""

import math

import pytest

from sprocket_engine.rpc import RpcError

RADIUS = 1.0


def top_edges(client):
    return [f"e{index}" for index, edge in enumerate(client.call("describe_geometry")["edges"]) if edge["centre"][2] == 10]


def fillet_with(selector):
    return {"id": "r1", "type": "fillet", "base": "e1", "radius": RADIUS, "edges": selector}


def widen(client, half_width):
    polygon = [[-half_width, -15], [half_width, -15], [half_width, 15], [-half_width, 15]]
    sketch = {"id": "s1", "type": "sketch", "plane": "XY", "polygon": polygon}
    return client.call("apply_edit", edits=[{"op": "update", "id": "s1", "feature": sketch}])


def test_picked_edges_become_a_selector_that_selects_exactly_them(client):
    answer = client.call("resolve_selection", handles=top_edges(client))
    assert answer["strategy"] == "new_since"
    assert answer["selector"]["select"][1] == {"op": "new_since", "feature": "e1"}
    # By what is level at the top, not by how many there are.
    assert answer["selector"]["select"][2] == {"op": "at_end", "axis": "Z", "end": "max"}
    assert answer["count"] == 4
    assert answer["selector"]["expected_count"] == 4


def test_a_fillet_made_from_a_pick_survives_a_change_of_dimension(client):
    """M4's done-when. A fillet that named its edges by index would round the
    wrong ones once the sketch changed, and one that named them by position
    would follow them only by luck."""
    answer = client.call("resolve_selection", handles=top_edges(client))
    built = client.call("apply_edit", edits=[{"op": "insert", "feature": fillet_with(answer["selector"])}])["built"]
    assert built["ok"] is True

    wider = widen(client, 30)["built"]
    assert wider["ok"] is True
    # A 60 x 30 x 10 box, its four top edges rounded: the corners differ a little
    # from the tube of the straight runs, so allow a small margin.
    perimeter = 2 * (60 + 30)
    expected = 60 * 30 * 10 - (1 - math.pi / 4) * RADIUS**2 * perimeter
    assert wider["volume"] == pytest.approx(expected, abs=2.0)


def test_a_selector_that_finds_a_different_number_fails_loudly(client):
    """A pentagon has five top edges. Rounding five when four were picked is the
    silent wrong answer expected_count exists to stop."""
    answer = client.call("resolve_selection", handles=top_edges(client))
    client.call("apply_edit", edits=[{"op": "insert", "feature": fillet_with(answer["selector"])}])
    pentagon = [[-20, -15], [20, -15], [25, 0], [20, 15], [-20, 15]]
    sketch = {"id": "s1", "type": "sketch", "plane": "XY", "polygon": pentagon}
    built = client.call("apply_edit", edits=[{"op": "update", "id": "s1", "feature": sketch}])["built"]
    assert built["ok"] is False
    assert built["failed_feature"] == "r1"


def test_a_picked_face_resolves_to_one_face(client):
    top = next(index for index, face in enumerate(client.call("describe_geometry")["faces"]) if face["centre"][2] == 10)
    answer = client.call("resolve_selection", handles=[f"f{top}"])
    assert answer["count"] == 1
    assert answer["selector"]["select"][0] == {"op": "faces"}


def test_a_set_with_no_rule_is_named_by_position_and_says_so(client):
    """One top edge and one bottom edge on different sides have nothing in common."""
    edges = client.call("describe_geometry")["edges"]
    top = next(index for index, edge in enumerate(edges) if edge["centre"][2] == 10)
    bottom = next(index for index, edge in enumerate(edges) if edge["centre"][2] == 0 and edge["centre"][1] < 0 and index != top)
    answer = client.call("resolve_selection", handles=[f"e{top}", f"e{bottom}"])
    assert answer["strategy"] == "position"
    assert answer["selector"]["select"][1]["op"] == "nearest_each"
    assert answer["count"] == 2


def test_with_no_handles_it_uses_the_selection_the_person_made(client):
    handles = top_edges(client)
    client.call("set_selection", handles=handles, revision=0)
    assert client.call("resolve_selection")["count"] == 4


def test_a_stale_selection_is_refused_instead_of_resolved(client):
    """Picked at an older revision than the part has now."""
    widen(client, 30)
    client.call("set_selection", handles=top_edges(client), revision=0)
    with pytest.raises(RpcError, match="stale"):
        client.call("resolve_selection")


def test_edges_a_rebuild_moved_leave_nothing_to_resolve(client):
    client.call("set_selection", handles=top_edges(client), revision=0)
    widen(client, 30)
    assert client.call("get_selection")["handles"] == []
    with pytest.raises(RpcError):
        client.call("resolve_selection")


def test_faces_and_edges_cannot_be_mixed(client):
    with pytest.raises(RpcError, match="not a mix"):
        client.call("resolve_selection", handles=["f0", "e0"])


def test_a_handle_the_part_does_not_have_is_refused(client):
    with pytest.raises(RpcError, match="out of range"):
        client.call("resolve_selection", handles=["e999"])
