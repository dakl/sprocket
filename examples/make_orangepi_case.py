"""Writes examples/orangepi-case.sprocket: an open-top printed tray around the
Orange Pi Zero 3W, which it shows as a reference body.

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
    "case_top": 12.0,  # UNMEASURED: wall top above the PCB underside; the fan bracket reaches 10.4
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
}

OUTER_Y = {"select": [{"op": "faces"}, {"op": "at_end", "axis": "Y", "end": "max"}], "expected_count": 1}
OUTER_X = {"select": [{"op": "faces"}, {"op": "at_end", "axis": "X", "end": "max"}], "expected_count": 1}
TOP = {"select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}], "expected_count": 1}


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
    # The tray: a block from the floor's underside to the wall top, hollowed out.
    {"id": "blocks", "type": "sketch", "plane": {"name": "XY", "offset": "=-(standoff_h+wall)"},
     "rect": {"width": "=pcb_l+2*(clearance+wall)", "height": "=pcb_w+2*(clearance+wall)", "center": ["=pcb_l/2", "=pcb_w/2"]}},
    {"id": "block", "type": "extrude", "profile": "blocks", "distance": "=wall+standoff_h+case_top"},
    {"id": "tray", "type": "shell", "base": "block", "thickness": "=wall", "open": TOP},
    # Screw posts on the 58 x 25 mm hole pattern: one circle, then a sketch pattern.
    {"id": "postss", "type": "sketch", "plane": {"name": "XY", "offset": "=-standoff_h"},
     "entities": [{"id": "p", "kind": "circle", "c": ["=hole_inset", "=hole_inset"], "r": "=post_d/2"}],
     "patterns": [{"entities": ["p"], "linear": {"count": 2, "step": ["=pcb_l-2*hole_inset", 0]},
                   "linear2": {"count": 2, "step": [0, "=pcb_w-2*hole_inset"]}}]},
    {"id": "posts", "type": "extrude", "profile": "postss", "base": "tray", "mode": "join", "distance": "=standoff_h"},
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
]

document = {
    "format_version": 2, "features": features, "rollback_index": len(features), "parameters": parameters,
    "appearances": [], "bodies": {"board": {"name": "Orange Pi Zero 3W"}, "block": {"name": "Case", "color": "#e07b39"}},
    "revision": 0, "log": [],
}
out = sys.argv[1] if len(sys.argv) > 1 else "examples/orangepi-case.sprocket"
json.dump(document, open(out, "w"), indent=1)
