"""How a client learns what it can pick: get_profiles, and the regions the
viewer reads out of features.json.
"""

import json

import pytest
from conftest import running
from test_mcp import drive

from sprocket_engine.document import Document
from sprocket_engine.rpc import Client, RpcError
from sprocket_engine.viewer_files import features_summary

RING_AND_DISC = [
    {"id": "e1", "kind": "line", "p0": [0, 0], "p1": [40, 0]},
    {"id": "e2", "kind": "line", "p0": [40, 0], "p1": [40, 20]},
    {"id": "e3", "kind": "line", "p0": [40, 20], "p1": [0, 20]},
    {"id": "e4", "kind": "line", "p0": [0, 20], "p1": [0, 0]},
    {"id": "e5", "kind": "circle", "c": [20, 10], "r": 4},
]
DOCUMENT = {
    "features": [
        {"id": "f1", "type": "sketch", "plane": "XY", "entities": RING_AND_DISC},
        {
            "id": "f2",
            "type": "sketch",
            "plane": "XY",
            "rect": {"width": 10, "height": 6},
        },
    ],
    "rollback_index": 2,
}


@pytest.fixture
def client(tmp_path):
    with running(DOCUMENT, tmp_path) as daemon, Client(daemon.socket_path) as connected:
        yield connected


def test_get_profiles_names_each_region_by_the_entities_that_bound_it(client):
    profiles = client.call("get_profiles", feature="f1")["profiles"]
    assert sorted(profile["bounded_by"] for profile in profiles) == [
        ["e1", "e2", "e3", "e4", "e5"],
        ["e5"],
    ]
    disc = next(profile for profile in profiles if profile["bounded_by"] == ["e5"])
    assert disc["area"] == pytest.approx(50.265482, abs=1e-5)
    assert len(disc["outline"]) > 20


def test_a_sketch_of_one_shape_is_one_profile_with_no_boundary_ids(client):
    (profile,) = client.call("get_profiles", feature="f2")["profiles"]
    assert profile["bounded_by"] == []
    assert profile["area"] == pytest.approx(60)
    assert len(profile["outline"]) == 4


def test_get_profiles_refuses_a_feature_that_is_not_a_sketch(tmp_path):
    document = {
        "features": [
            *DOCUMENT["features"],
            {"id": "f3", "type": "extrude", "profile": "f2", "distance": 4},
        ]
    }
    with running(document, tmp_path) as daemon, Client(daemon.socket_path) as client:
        with pytest.raises(RpcError, match="not a sketch"):
            client.call("get_profiles", feature="f3")
        with pytest.raises(RpcError, match="no feature with id 'nope'"):
            client.call("get_profiles", feature="nope")


def test_the_viewer_gets_regions_for_every_sketch(tmp_path):
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps(DOCUMENT))
    entries = features_summary(Document.load(path), None)
    assert [len(entry["regions"]) for entry in entries] == [2, 1]


def test_a_parametric_sketch_reaches_the_viewer_as_numbers_and_keeps_its_expressions(
    tmp_path,
):
    entities = json.loads(
        json.dumps(RING_AND_DISC)
        .replace("[40, 0]", '["=w", 0]')
        .replace('"r": 4', '"r": "=w/10"')
    )
    document = {
        "parameters": {"w": 40},
        "features": [
            {"id": "f1", "type": "sketch", "plane": "XY", "entities": entities}
        ],
    }
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps(document))
    (entry,) = features_summary(Document.load(path), None)
    # The form edits the raw feature, so the expression stays there.
    assert entry["feature"]["entities"][4]["r"] == "=w/10"
    # The viewer draws from `resolved`, which must hold only numbers.
    assert entry["resolved"]["entities"][4]["r"] == 4
    assert entry["resolved"]["entities"][0]["p1"] == [40, 0]
    assert len(entry["regions"]) == 2


def test_a_sketch_the_engine_cannot_read_still_lists_with_no_regions(tmp_path):
    broken = {
        "features": [
            {
                "id": "f1",
                "type": "sketch",
                "plane": "XY",
                "entities": [{"id": "e1", "kind": "arc", "c": [0, 0], "r": 1}],
            }
        ]
    }
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps(broken))
    (entry,) = features_summary(Document.load(path), None)
    assert entry["regions"] == []


def test_an_agent_reads_profiles_through_mcp_and_extrudes_one(tmp_path):
    async def work(session):
        listed = await session.call_tool("get_profiles", {"feature": "f1"})
        profiles = json.loads(listed.content[0].text)["profiles"]
        disc = next(profile for profile in profiles if profile["bounded_by"] == ["e5"])
        added = await session.call_tool(
            "add_extrude",
            {
                "profiles": [{"sketch": "f1", "bounded_by": disc["bounded_by"]}],
                "distance": 5,
            },
        )
        return added, await session.call_tool("measure", {})

    with running(DOCUMENT, tmp_path) as daemon:
        added, measured = drive(daemon, work)
    assert not added.is_error, added.content
    assert json.loads(measured.content[0].text)["volume"] == pytest.approx(
        3.141592653589793 * 16 * 5, rel=1e-6
    )
