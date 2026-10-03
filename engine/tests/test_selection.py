"""The person's selection, as the daemon holds it for an agent (M4.5).

Another client stands in for the agent. A notification reaches a client on its
next call, so each test pings the listener to read what was sent.
"""

import pytest

from sprocket_engine.rpc import Client, RpcError


@pytest.fixture
def listener(daemon):
    seen = []
    with Client(daemon.socket_path, on_notification=lambda method, params: seen.append((method, params))) as connected:
        # The daemon registers a connection on its own thread, after connect()
        # returns. A reply proves it has, so nothing sent next is missed.
        connected.call("ping")
        connected.seen = seen
        yield connected


def heard(listener, method):
    listener.call("ping")
    return [params for name, params in listener.seen if name == method]


def test_what_the_viewer_picks_is_what_an_agent_reads(client):
    client.call("set_selection", handles=["f0", "e1"], revision=0)
    selection = client.call("get_selection")
    assert selection["handles"] == ["f0", "e1"]
    assert selection["stale"] is False
    assert [item["handle"] for item in selection["descriptions"]] == ["f0", "e1"]
    assert all(item["description"] for item in selection["descriptions"])
    # Enough to build a selector from: where it is.
    assert all(len(item["centre"]) == 3 for item in selection["descriptions"])


def test_a_rebuild_makes_the_selection_stale_and_drops_the_sentences(client):
    """f2 may be another face after an edit, so a sentence about it would mislead."""
    client.call("set_selection", handles=["f2"], revision=0)
    client.call("set_parameter", name="width", value=40)
    selection = client.call("get_selection")
    assert selection["handles"] == ["f2"]
    assert selection["stale"] is True
    assert "descriptions" not in selection


def test_describe_geometry_carries_the_selection(client):
    client.call("set_selection", handles=["f3"], revision=0)
    assert client.call("describe_geometry")["selection"]["handles"] == ["f3"]


def test_a_new_pick_replaces_the_old_one_and_an_empty_pick_clears_it(client):
    client.call("set_selection", handles=["f0"], revision=0)
    client.call("set_selection", handles=["f1"], revision=0)
    assert client.call("get_selection")["handles"] == ["f1"]
    client.call("set_selection", handles=[], revision=0)
    assert client.call("get_selection")["handles"] == []


def test_a_pick_is_announced_to_the_other_clients(client, listener):
    client.call("set_selection", handles=["f0"], sketches=["s1"], revision=0)
    announced = heard(listener, "selection_changed")
    assert announced[-1]["handles"] == ["f0"]
    assert announced[-1]["sketches"] == ["s1"]


def test_sketch_ids_that_are_not_sketches_are_left_out(client):
    client.call("set_selection", sketches=["s1", "e1", "nope"], revision=0)
    assert client.call("get_selection")["sketches"] == ["s1"]


def test_a_handle_that_is_not_a_handle_is_refused(client):
    with pytest.raises(RpcError):
        client.call("set_selection", handles=["face 3"])
    assert client.call("get_selection")["handles"] == []


def test_highlight_reaches_the_viewer_and_leaves_the_selection_alone(client, listener):
    client.call("set_selection", handles=["f0"], revision=0)
    client.call("highlight", handles=["f1", "e2"])
    assert heard(listener, "highlight")[-1]["handles"] == ["f1", "e2"]
    assert client.call("get_selection")["handles"] == ["f0"]


def test_highlight_refuses_a_handle_the_part_does_not_have(client, listener):
    with pytest.raises(RpcError, match="out of range"):
        client.call("highlight", handles=["f999"])
    assert heard(listener, "highlight") == []


def test_an_empty_highlight_clears_it(client, listener):
    client.call("highlight", handles=[])
    assert heard(listener, "highlight")[-1] == {"handles": [], "sketches": []}


def test_opening_another_document_clears_the_selection(client, listener, tmp_path):
    client.call("set_selection", handles=["f0"], revision=0)
    client.call("new_document", path=str(tmp_path / "other.sprocket"))
    assert client.call("get_selection")["handles"] == []
    assert heard(listener, "selection_changed")[-1]["handles"] == []


def test_fillet_these_can_be_built_from_what_the_agent_reads(client):
    """M4.5's done-when: the person picks an edge, the agent reads it and fillets it."""
    edge = next(item for item in client.call("describe_geometry")["edges"] if item["centre"][2] == 10)
    client.call("set_selection", handles=[edge["handle"]], revision=0)

    picked = client.call("get_selection")["descriptions"][0]
    fillet = {
        "id": "f1",
        "type": "fillet",
        "base": "e1",
        "radius": 1,
        "edges": {"select": [{"op": "edges"}, {"op": "nearest_to", "point": picked["centre"]}]},
    }
    result = client.call("apply_edit", edits=[{"op": "insert", "feature": fillet}])
    assert result["built"]["ok"] is True
    assert result["built"]["volume"] < 12000.0
