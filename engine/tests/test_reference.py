"""Reference bodies: another document or a STEP file shown for design, never exported."""

import json

import pytest
from build123d import Box, export_step, import_step

from conftest import running
from sprocket_engine.document import Document
from sprocket_engine.export import write_step
from sprocket_engine.mesh import export_mesh
from sprocket_engine.replay import replay, with_settings
from sprocket_engine.rpc import Client

BOARD = {"features": [
    {"id": "s", "type": "sketch", "plane": "XY", "rect": {"width": 10, "height": 10}},
    {"id": "b", "type": "extrude", "profile": "s", "distance": 2},
]}
TRAY = [
    {"id": "ts", "type": "sketch", "plane": {"name": "XY", "offset": -3}, "rect": {"width": 20, "height": 20}},
    {"id": "tray", "type": "extrude", "profile": "ts", "distance": 4},
]


def write(tmp_path, name, document):
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def build(tmp_path, features):
    document = Document.load(write(tmp_path, "case.sprocket", {"features": features}))
    part, ctx = replay(document.for_replay(), return_context=True)
    return document, part, ctx


def test_a_referenced_document_is_a_reference_body_left_out_of_the_part(tmp_path):
    write(tmp_path, "board.sprocket", BOARD)
    _, part, ctx = build(tmp_path, [{"id": "ref", "type": "reference", "file": "board.sprocket"}] + TRAY)
    assert [(body["id"], body["reference"]) for body in ctx.bodies] == [("ref", True), ("tray", False)]
    assert part.volume == pytest.approx(20 * 20 * 4)
    assert ctx.bodies[0]["shape"].volume == pytest.approx(200)


def test_the_mesh_puts_reference_bodies_after_the_part_and_step_leaves_them_out(tmp_path):
    write(tmp_path, "board.sprocket", BOARD)
    _, part, ctx = build(tmp_path, [{"id": "ref", "type": "reference", "file": "board.sprocket"}] + TRAY)
    bodies = with_settings(ctx.bodies, {})
    header = export_mesh(part, tmp_path / "part.mesh", None, bodies)
    assert [(body["id"], body["reference"]) for body in header["bodies"]] == [("tray", False), ("ref", True)]
    assert header["counts"]["faces"] == 12
    write_step(part, tmp_path / "part.step", bodies)
    assert import_step(tmp_path / "part.step").volume == pytest.approx(1600)


def test_a_step_reference_moves_by_translate(tmp_path):
    export_step(Box(2, 2, 2), tmp_path / "cube.step")
    _, _, ctx = build(tmp_path, [{"id": "ref", "type": "reference", "file": "cube.step", "translate": [5, 0, 0]}])
    assert ctx.bodies[0]["shape"].center().X == pytest.approx(5)


def test_cutting_with_a_reference_keeps_it_on_screen(tmp_path):
    write(tmp_path, "board.sprocket", BOARD)
    cut = {"id": "fit", "type": "boolean", "operation": "subtract", "base": "tray", "tool": "ref"}
    _, part, ctx = build(tmp_path, [{"id": "ref", "type": "reference", "file": "board.sprocket"}] + TRAY + [cut])
    assert [body["id"] for body in ctx.bodies] == ["ref", "tray"]
    assert part.volume == pytest.approx(1600 - 10 * 10 * 1)  # the board's lower 1 mm is inside the tray


def test_a_document_that_references_itself_fails_with_a_message(tmp_path):
    path = write(tmp_path, "loop.sprocket", {"features": [{"id": "ref", "type": "reference", "file": "loop.sprocket"}]})
    with pytest.raises(ValueError, match="includes itself"):
        replay(Document.load(path).for_replay())


def test_the_cache_key_follows_the_referenced_file(tmp_path):
    board = write(tmp_path, "board.sprocket", BOARD)
    document = Document.load(write(tmp_path, "case.sprocket", {"features": [{"id": "ref", "type": "reference", "file": "board.sprocket"}]}))
    before = document.for_replay()["features"][0]["source_sha256"]
    board.write_text(json.dumps(BOARD).replace('"distance": 2', '"distance": 3'))
    assert document.for_replay()["features"][0]["source_sha256"] != before


def test_check_clearance_reports_distance_and_overlap(tmp_path):
    write(tmp_path, "board.sprocket", BOARD)
    features = [{"id": "ref", "type": "reference", "file": str(tmp_path / "board.sprocket")}] + TRAY
    with running({"features": features}, tmp_path) as daemon, Client(daemon.socket_path) as client:
        (pair,) = client.call("check_clearance")["pairs"]
    assert pair["body"] == "tray" and pair["reference"] == "ref"
    assert pair["distance"] == 0
    assert pair["overlap_volume"] == pytest.approx(100)


def test_the_orange_pi_case_example_wraps_the_board_without_touching_it_inside(tmp_path):
    """examples/orangepi-case.sprocket: the board is a reference body, the case
    the only exported body, and they share no volume."""
    from pathlib import Path

    from build123d import ShapeList

    document = Document.load(Path(__file__).parents[2] / "examples" / "orangepi-case.sprocket")
    part, ctx = replay(document.for_replay(), return_context=True)
    assert [(body["id"], body["reference"]) for body in ctx.bodies] == [("board", True), ("block", False)]
    board, case = ctx.bodies[0]["shape"], ctx.bodies[1]["shape"]
    assert part.volume == pytest.approx(case.volume)
    shared = case.intersect(board)
    pieces = list(shared) if isinstance(shared, ShapeList) else [shared] if shared is not None else []
    assert sum(piece.volume for piece in pieces) == pytest.approx(0, abs=1e-6)
    box = case.bounding_box()
    assert (box.min.X, box.min.Y, box.max.X, box.max.Y) == pytest.approx((-3, -3, 68, 35))


def test_the_orange_pi_green_face_entry_selects_exactly_the_picked_face():
    """The last appearance in examples/orangepi-zero-3w.sprocket colours the
    one face Daniel picked: pin 28's cell, -Y side, area 2.24 x 2.5."""
    from pathlib import Path

    from sprocket_engine.selectors import resolve_selector

    document = Document.load(Path(__file__).parents[2] / "examples" / "orangepi-zero-3w.sprocket")
    shape, ctx = replay(document.for_replay(), return_context=True)
    entry = document.appearances[-1]
    assert entry["color"] == "#2f9e44"
    (face,) = resolve_selector(shape, entry["select"], ctx)
    assert tuple(face.center()) == pytest.approx((23.61, 0.15, 2.45), abs=0.01)
    assert tuple(face.normal_at()) == pytest.approx((0, -1, 0))
    assert face.area == pytest.approx(5.6)
