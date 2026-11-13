"""Section: where a plane cuts the part, over the daemon on the 40 x 30 x 10
conftest box (x -20 to 20, y -15 to 15, z 0 to 10), and the drawings it writes."""

import math

import pytest
from build123d import Align, Box, Cylinder, Plane

from sprocket_engine.export import section_of
from sprocket_engine.rpc import RpcError


def test_a_vertical_cut_through_the_middle_is_40_by_10_in_the_planes_own_frame(client):
    cut = client.call("section", plane="XZ")
    assert (cut["faces"], cut["area"]) == (1, pytest.approx(400))
    # XZ's x_dir is +X and its v axis is +Z, so the cut reads as the box's front view.
    assert cut["bounding_box"] == {
        "min": [pytest.approx(-20), pytest.approx(0)],
        "max": [pytest.approx(20), pytest.approx(10)],
    }


def test_a_face_plane_with_an_offset_cuts_inside_the_part(client):
    top = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
        "expected_count": 1,
    }
    assert client.call("section", plane={"feature": "e1", "face": top, "offset": -5})[
        "area"
    ] == pytest.approx(1200)


def test_a_plane_that_misses_reports_nothing_and_writes_nothing(client, tmp_path):
    missed = client.call("section", plane={"name": "XY", "offset": 50})
    assert (missed["faces"], missed["area"]) == (0, 0.0)
    assert "bounding_box" not in missed
    with pytest.raises(RpcError, match="the plane misses the part"):
        client.call(
            "section", plane={"name": "XY", "offset": 50}, path=str(tmp_path / "s.svg")
        )


def test_it_writes_svg_and_dxf_and_refuses_other_files(client, tmp_path):
    svg, dxf = tmp_path / "cut.svg", tmp_path / "cut.dxf"
    assert client.call("section", plane={"name": "XY", "offset": 5}, path=str(svg))[
        "written"
    ] == str(svg)
    assert "<path" in svg.read_text()
    client.call("section", plane={"name": "XY", "offset": 5}, path=str(dxf))
    assert "LINE" in dxf.read_text()
    with pytest.raises(RpcError, match="must end in .dxf or .svg"):
        client.call("section", plane="XY", path=str(tmp_path / "cut.png"))


def test_a_cut_through_a_hole_leaves_the_hole_out():
    plate = Box(20, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(
        3, 4, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    cut = section_of(plate, Plane.XY.offset(2))
    assert cut.area == pytest.approx(200 - 9 * math.pi)
    assert len(cut.faces()[0].inner_wires()) == 1
