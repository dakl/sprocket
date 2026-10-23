"""The measure method over the daemon, on the 40 x 30 x 10 conftest box."""

import pytest

from sprocket_engine.rpc import RpcError


def handles(client):
    described = client.call("describe_geometry")
    faces = {tuple(face["normal"]): face["handle"] for face in described["faces"]}
    vertical = [
        edge["handle"]
        for edge in described["edges"]
        if edge["length"] == pytest.approx(10)
    ]
    long_edges = [
        edge for edge in described["edges"] if edge["length"] == pytest.approx(40)
    ]
    return faces, vertical, long_edges


def test_top_and_bottom_face_each_other_10_mm_apart(client):
    faces, _, _ = handles(client)
    measured = client.call(
        "measure", handles=[faces[(0.0, 0.0, 1.0)], faces[(0.0, 0.0, -1.0)]]
    )
    assert measured["distance"] == pytest.approx(10)
    assert measured["angle"] == pytest.approx(180)


def test_side_faces_meet_at_a_right_angle_and_touch(client):
    faces, _, _ = handles(client)
    measured = client.call(
        "measure", handles=[faces[(1.0, 0.0, 0.0)], faces[(0.0, 1.0, 0.0)]]
    )
    assert (measured["distance"], measured["angle"]) == (
        pytest.approx(0),
        pytest.approx(90),
    )


def test_an_edge_to_a_face_is_the_line_to_plane_angle(client):
    faces, vertical, _ = handles(client)
    measured = client.call("measure", handles=[faces[(0.0, 0.0, 1.0)], vertical[0]])
    assert measured["angle"] == pytest.approx(90)


def test_two_long_edges_are_parallel_and_the_distance_is_the_gap(client):
    _, _, long_edges = handles(client)
    bottom = [edge for edge in long_edges if edge["centre"][2] == pytest.approx(0)]
    measured = client.call(
        "measure", handles=[bottom[0]["handle"], bottom[1]["handle"]]
    )
    assert (measured["angle"], measured["distance"]) == (
        pytest.approx(0),
        pytest.approx(30),
    )


def test_the_whole_part_lists_each_body(client):
    measured = client.call("measure")
    assert measured["volume"] == pytest.approx(12000)
    assert measured["bodies"] == [
        {
            "id": "e1",
            "name": "e1",
            "volume": pytest.approx(12000),
            "area": pytest.approx(2 * (1200 + 400 + 300)),
        }
    ]


def test_one_body_by_id(client):
    assert client.call("measure", body="e1")["volume"] == pytest.approx(12000)
    with pytest.raises(RpcError, match="no part body 'nope'; part bodies are e1"):
        client.call("measure", body="nope")
