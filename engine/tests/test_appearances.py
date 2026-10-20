import pytest
from build123d import Box

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.mesh import NO_COLOUR, export_mesh, face_colours

TOP = {"select": [{"op": "faces"}, {"op": "within", "box": [-1, -1, 4.9, 1, 1, 5.1]}]}


def test_within_colours_only_the_face_inside_the_box():
    colours = face_colours(Box(2, 2, 10), [{"color": "#ff0000", "select": TOP}])
    assert sorted(colours.tolist()) == [0xFF0000] + [NO_COLOUR] * 5


def test_a_later_appearance_wins():
    everything = {"select": [{"op": "faces"}]}
    colours = face_colours(
        Box(2, 2, 10),
        [
            {"color": "#00ff00", "select": everything},
            {"color": "#ff0000", "select": TOP},
        ],
    )
    assert sorted(colours.tolist()) == [0x00FF00] * 5 + [0xFF0000]


def test_mesh_carries_the_colour_buffer_only_when_asked(tmp_path):
    plain = export_mesh(Box(2, 2, 10), tmp_path / "plain.mesh")
    painted = export_mesh(
        Box(2, 2, 10), tmp_path / "painted.mesh", [{"color": "#ff0000", "select": TOP}]
    )
    assert "face_colors" not in plain["buffers"]
    assert painted["buffers"]["face_colors"]["length"] == 6


def test_document_rejects_a_bad_colour(tmp_path):
    path = tmp_path / "bad.sprocket"
    path.write_text(
        '{"features": [], "appearances": [{"color": "red", "select": {"select": []}}]}'
    )
    with pytest.raises(DocumentError, match="appearances"):
        Document.load(path)
