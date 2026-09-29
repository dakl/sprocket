"""The rasteriser is the agent's eyes, so the tests read the pixels back.

Every check here decodes the PNG rather than asserting on the return value.
The first version of this renderer reported six faces and 144 edges quite
happily while drawing a box as a flat grey hexagon, and nothing short of
looking at the pixels catches that.
"""

import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from sprocket_engine.render import (
    BACKGROUND,
    RenderError,
    render_png,
)
from sprocket_engine.replay import replay

BOX = {
    "features": [
        {"id": "s1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 30}},
        {"id": "e1", "type": "extrude", "profile": "s1", "distance": 10},
    ],
    "rollback_index": 2,
}


def read_png(path: Path) -> np.ndarray:
    """Decodes what `render.py` writes: 8-bit RGB, filter 0 on every row."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    chunks = {}
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        chunks.setdefault(kind, b"")
        chunks[kind] += data[offset + 8:offset + 8 + length]
        offset += length + 12

    width, height, depth, colour_type = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, colour_type) == (8, 2)
    raw = zlib.decompress(chunks[b"IDAT"])
    stride = width * 3 + 1
    rows = []
    for index in range(height):
        row = raw[index * stride:(index + 1) * stride]
        assert row[0] == 0, "this decoder only handles filter type 0"
        rows.append(np.frombuffer(row[1:], dtype=np.uint8))
    return np.stack(rows).reshape(height, width, 3)


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    return replay(BOX, cache_dir=tmp_path_factory.mktemp("cache"))


def dominant_colours(image: np.ndarray, floor: int = 1000) -> list[tuple]:
    """The colours that cover a real area, which is one per flat-shaded face.

    Supersampling leaves a thin scatter of blended pixels along every fold, so
    a bare count of distinct colours says nothing. The populous ones are the
    face interiors.
    """
    flat = image.reshape(-1, 3)
    colours, counts = np.unique(flat, axis=0, return_counts=True)
    return [
        tuple(int(channel) for channel in colour)
        for colour, count in zip(colours, counts)
        if count >= floor and tuple(colour) != BACKGROUND
    ]


def read_png_of(shape, path: Path, **kwargs) -> np.ndarray:
    render_png(shape, path, **kwargs)
    return read_png(path)


def test_an_iso_box_shows_three_distinct_faces(box, tmp_path: Path):
    """The whole reason the light sits off the camera axis.

    A headlight shades every visible face of a box 0.577 on the iso view, so
    the part renders as one flat hexagon and an agent cannot tell a face from
    a fold. Three faces are visible from here, so three shades.
    """
    image = read_png_of(box, tmp_path / "iso.png", camera="iso")
    assert len(dominant_colours(image)) == 3


def test_edges_are_drawn_over_the_faces(box, tmp_path: Path):
    """Without the outline a fillet and a chamfer are the same grey lump.

    Seen from above a box is one flat shade, so any pixel darker than that one
    shade is the edge overlay and nothing else. Supersampling blends every edge
    pixel with the face under it, so no pixel comes back at EDGE_COLOUR itself.
    """
    image = read_png_of(box, tmp_path / "top.png", camera="top")
    (face,) = dominant_colours(image)
    darker = (image.max(axis=2) < max(face) - 5).sum()
    assert darker > 500


def test_highlight_paints_the_face_geometry_names(box, tmp_path: Path):
    """One face ordering across the system.

    `geometry.describe_faces` calls one face the front of this part. If the two
    ever disagree, an agent highlights one face and the user sees another.
    """
    from sprocket_engine.geometry import describe_faces

    front = next(face for face in describe_faces(box) if "front" in face["description"])
    image = read_png_of(box, tmp_path / "front.png", camera="front", highlight=[front["handle"]])
    lit = np.any(image != np.array(BACKGROUND, dtype=np.uint8), axis=2)
    # The part colour is a blue-grey, the highlight an orange, so the sign of
    # red minus blue separates them whatever the shading does to the brightness.
    warm = (image[:, :, 0].astype(int) - image[:, :, 2].astype(int)) > 30
    # Seen head on, the highlighted face is essentially the whole silhouette.
    assert (warm & lit).sum() > 0.8 * lit.sum()

    plain = read_png_of(box, tmp_path / "plain.png", camera="front")
    plain_lit = np.any(plain != np.array(BACKGROUND, dtype=np.uint8), axis=2)
    plain_warm = (plain[:, :, 0].astype(int) - plain[:, :, 2].astype(int)) > 30
    assert (plain_warm & plain_lit).sum() == 0


def test_a_render_reports_what_it_drew(box, tmp_path: Path):
    info = render_png(box, tmp_path / "iso.png", camera="top", width=200, height=160)
    assert (info["width"], info["height"], info["camera"]) == (200, 160, "top")
    assert info["faces"] == len(box.faces())
    assert read_png(tmp_path / "iso.png").shape == (160, 200, 3)


def test_opposite_cameras_do_not_render_the_same_image(box, tmp_path: Path):
    """A camera direction that is ignored, or applied with the wrong sign,
    still produces a plausible picture. Two of them disagreeing is the check."""
    top = read_png_of(box, tmp_path / "top.png", camera="top")
    front = read_png_of(box, tmp_path / "front.png", camera="front")
    assert not np.array_equal(top, front)


def test_an_unknown_camera_is_refused(box, tmp_path: Path):
    with pytest.raises(RenderError, match="camera"):
        render_png(box, tmp_path / "nope.png", camera="sideways")


def test_a_silly_size_is_refused(box, tmp_path: Path):
    with pytest.raises(RenderError):
        render_png(box, tmp_path / "nope.png", width=4, height=4)


def test_an_out_of_range_highlight_handle_is_refused(box, tmp_path: Path):
    with pytest.raises(RenderError):
        render_png(box, tmp_path / "nope.png", highlight=["f99"])
