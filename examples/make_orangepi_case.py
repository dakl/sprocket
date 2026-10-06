"""Writes examples/orangepi-case.sprocket: a printed case around the Orange Pi
Zero 3W, split into a base and a lid that locate on a lip. The board is shown
as a reference body.

The board's own numbers (65 x 32 mm, holes 3.5 mm in from the edges, so a
58 x 25 mm pattern) are the vendor spec and the Pi Zero pattern, as in
make_orangepi_zero_3w.py. Every case number and every port position is an
estimate, marked UNMEASURED, until checked against the real board and a test
print. Same origin as the board: PCB corner at the header edge, X along the
65 mm side, Y toward the port edge, Z up from the PCB underside.
"""
import json
import sys

parameters = {
    "pcb_l": 65,  # spec, as in the board document
    "pcb_w": 32,  # spec
    "pcb_t": 1.2,  # spec
    "hole_inset": 3.5,  # UNMEASURED: Pi Zero pattern
    "clearance": 1.0,  # UNMEASURED: gap from the board edge to the wall; the ports stick out 0.5 mm
    "wall": 2.0,  # wall and floor thickness
    "standoff_h": 3.0,  # UNMEASURED: under the board, clears the 1.8 mm microSD socket
    "case_top": 13.0,  # UNMEASURED: lid top above the PCB underside; the fan bracket reaches 10.4, the ceiling is case_top - wall
    "post_d": 5.5,  # UNMEASURED
    "pilot_d": 2.2,  # UNMEASURED: M2.5 self-tapping pilot
    "port_margin": 1.0,  # UNMEASURED: cut-out margin around each connector
    "usb_w": 9.0,  # UNMEASURED, as in the board document
    "usb_h": 3.3,  # UNMEASURED
    "hdmi_w": 11.6,  # UNMEASURED
    "hdmi_h": 3.3,  # UNMEASURED
    "sd_y": 16.75,  # UNMEASURED: microSD socket centre along Y, from the underside photo
    "sd_w": 11.3,  # UNMEASURED: socket width along Y (y 11.1 to 22.4)
    "sd_h": 1.8,  # UNMEASURED: socket height below the PCB
    "split_z": 7.0,  # UNMEASURED: base/lid parting height above the PCB underside, above the ports (top 5.5)
    "lip_h": 2.0,  # UNMEASURED: how far the base's lip stands into the lid
    "lip_t": 0.8,  # UNMEASURED: lip thickness, the inner part of the wall
    "lip_fit": 0.15,  # UNMEASURED: print clearance between the lip and the lid's groove, per side and on top
    "draft_angle": 1.0,  # UNMEASURED: degrees on the outer walls, narrowing upward, so the part lets go of a bed or mould
}

OUTER_Y = {"select": [{"op": "faces"}, {"op": "at_end", "axis": "Y", "end": "max"}], "expected_count": 1}
OUTER_X = {"select": [{"op": "faces"}, {"op": "at_end", "axis": "X", "end": "max"}], "expected_count": 1}
# The outer side faces, by their centres: each sits at the outside of a wall, at
# the case's mid-height. The inner faces are a wall's thickness away.
MID_Z = "(case_top-standoff_h-wall)/2"
OUTER_WALLS = {"select": [{"op": "faces"}, {"op": "nearest_each", "points": [
    ["=-clearance-wall", "=pcb_w/2", f"={MID_Z}"], ["=pcb_l+clearance+wall", "=pcb_w/2", f"={MID_Z}"],
    ["=pcb_l/2", "=-clearance-wall", f"={MID_Z}"], ["=pcb_l/2", "=pcb_w+clearance+wall", f"={MID_Z}"]]}],
    "expected_count": 4}


PARTING = {"name": "XY", "offset": "=split_z"}


def cavity(grow):
    """The inside of the case in plan, grown by `grow` (an expression) in total."""
    return {"width": f"=pcb_l+2*clearance+{grow}", "height": f"=pcb_w+2*clearance+{grow}", "center": ["=pcb_l/2", "=pcb_w/2"]}


def port(entity_prefix, u, v, width, height):
    """A rectangle of four lines centred on (u, v), sides `width` and `height` (expressions)."""
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    points = [[f"={u}{'+' if sx > 0 else '-'}({width})/2", f"={v}{'+' if sy > 0 else '-'}({height})/2"] for sx, sy in corners]
    return [
        {"id": f"{entity_prefix}{k}", "kind": "line", "p0": points[k], "p1": points[(k + 1) % 4]}
        for k in range(4)
    ]


features = [
    # The board, shown translucent and never exported.
    {"id": "board", "type": "reference", "file": "orangepi-zero-3w.sprocket"},
    # The case: a block from the floor's underside to the lid top, hollowed out
    # closed, then split at split_z into a base (here) and a lid (below).
    {"id": "blocks", "type": "sketch", "plane": {"name": "XY", "offset": "=-(standoff_h+wall)"},
     "rect": {"width": "=pcb_l+2*(clearance+wall)", "height": "=pcb_w+2*(clearance+wall)", "center": ["=pcb_l/2", "=pcb_w/2"]}},
    {"id": "block", "type": "extrude", "profile": "blocks", "distance": "=wall+standoff_h+case_top"},
    {"id": "tray", "type": "shell", "base": "block", "thickness": "=wall"},
    # The four outer walls lean in by draft_angle about the floor's underside.
    # Only the outside: the cavity stays square to the board.
    {"id": "taper", "type": "draft", "base": "tray", "angle": "=draft_angle",
     "neutral": {"name": "XY", "offset": "=-(standoff_h+wall)"}, "faces": OUTER_WALLS},
    # Screw posts on the 58 x 25 mm hole pattern: one circle, then a sketch pattern.
    {"id": "postss", "type": "sketch", "plane": {"name": "XY", "offset": "=-standoff_h"},
     "entities": [{"id": "p", "kind": "circle", "c": ["=hole_inset", "=hole_inset"], "r": "=post_d/2"}],
     "patterns": [{"entities": ["p"], "linear": {"count": 2, "step": ["=pcb_l-2*hole_inset", 0]},
                   "linear2": {"count": 2, "step": [0, "=pcb_w-2*hole_inset"]}}]},
    {"id": "posts", "type": "extrude", "profile": "postss", "base": "taper", "mode": "join", "distance": "=standoff_h"},
    {"id": "pilots", "type": "hole", "base": "posts", "diameter": "=pilot_d", "depth": "=standoff_h+wall/2",
     "points": [["=hole_inset", "=hole_inset", 0], ["=pcb_l-hole_inset", "=hole_inset", 0],
                ["=hole_inset", "=pcb_w-hole_inset", 0], ["=pcb_l-hole_inset", "=pcb_w-hole_inset", 0]]},
    # Port cut-outs in the +Y wall, sketched on its outer face. On that face u
    # runs along world -X and v is world Z, so u = -x. Positions UNMEASURED,
    # copied from the board document.
    {"id": "portss", "type": "sketch", "plane": {"feature": "pilots", "face": OUTER_Y},
     "entities": port("u1_", -11.5, "pcb_t+usb_h/2", "usb_w+2*port_margin", "usb_h+2*port_margin")
     + port("u2_", -23.2, "pcb_t+usb_h/2", "usb_w+2*port_margin", "usb_h+2*port_margin")
     + port("h_", -52.7, "pcb_t+hdmi_h/2", "hdmi_w+2*port_margin", "hdmi_h+2*port_margin")},
    {"id": "ports", "type": "extrude", "profile": "portss", "base": "pilots", "mode": "cut", "distance": "=-(wall+clearance)"},
    # microSD slot in the +X wall, under the board: the socket sits at that end
    # and the card slides out toward +X. On that face u = y and v = z.
    {"id": "sds", "type": "sketch", "plane": {"feature": "ports", "face": OUTER_X},
     "entities": port("s_", "sd_y", "-sd_h/2", "sd_w+2*port_margin", "sd_h+2*port_margin")},
    {"id": "sd", "type": "extrude", "profile": "sds", "base": "ports", "mode": "cut", "distance": "=-(wall+clearance)"},
    # Cutting the board out of the case notches whatever it touches. With the
    # backside parts at their photo positions (2026-10-05) nothing does: only
    # the PCB rests on the posts. It stays as a guard while those positions are
    # UNMEASURED. A reference body stays on screen after the cut.
    {"id": "fit", "type": "boolean", "operation": "subtract", "base": "sd", "tool": "board"},
    {"id": "base", "type": "split", "base": "fit", "plane": PARTING, "keep": "below"},
    # A lip on the inner part of the base's wall rim, which the lid's groove takes.
    {"id": "lips", "type": "sketch", "plane": PARTING, "rect": cavity("2*lip_t")},
    {"id": "lip", "type": "extrude", "profile": "lips", "base": "base", "mode": "join", "distance": "=lip_h"},
    {"id": "lipbores", "type": "sketch", "plane": PARTING, "rect": cavity("0")},
    {"id": "lipbore", "type": "extrude", "profile": "lipbores", "base": "lip", "mode": "cut", "distance": "=lip_h"},
    # The lid: the same drafted closed shell, kept above the parting plane, with
    # a groove the lip slides into. A split cannot fork a body, so the lid is
    # its own body built from the same sketch.
    {"id": "lid_block", "type": "extrude", "profile": "blocks", "distance": "=wall+standoff_h+case_top"},
    {"id": "lid_shell", "type": "shell", "base": "lid_block", "thickness": "=wall"},
    {"id": "lid_taper", "type": "draft", "base": "lid_shell", "angle": "=draft_angle",
     "neutral": {"name": "XY", "offset": "=-(standoff_h+wall)"}, "faces": OUTER_WALLS},
    {"id": "lid", "type": "split", "base": "lid_taper", "plane": PARTING, "keep": "above"},
    {"id": "grooves", "type": "sketch", "plane": PARTING, "rect": cavity("2*(lip_t+lip_fit)")},
    {"id": "groove", "type": "extrude", "profile": "grooves", "base": "lid", "mode": "cut", "distance": "=lip_h+lip_fit"},
]

document = {
    "format_version": 2, "features": features, "rollback_index": len(features), "parameters": parameters,
    "appearances": [], "bodies": {"board": {"name": "Orange Pi Zero 3W"}, "block": {"name": "Case base", "color": "#e07b39"},
               "lid_block": {"name": "Lid", "color": "#e07b39"}},
    "revision": 0, "log": [],
}
out = sys.argv[1] if len(sys.argv) > 1 else "examples/orangepi-case.sprocket"
json.dump(document, open(out, "w"), indent=1)
