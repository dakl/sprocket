"""Writes examples/orangepi-zero-3w.sprocket.

Every number is either the vendor spec table (PCB 65 x 32 x 1.2) or an estimate.
Estimates come from the vendor top-view picture and the Pi Zero hole pattern,
and stay marked UNMEASURED until checked with calipers. Origin: PCB corner at
the header edge, X along the 65 mm side, Y toward the port edge, Z up from the
PCB underside.
"""
import json
import sys

parameters = {
    "pcb_l": 65,  # spec
    "pcb_w": 32,  # spec
    "pcb_t": 1.2,  # spec
    "hole_inset": 3.5,  # UNMEASURED: Pi Zero pattern, agrees with the picture
    "hole_d": 2.75,  # UNMEASURED: M2.5 clearance
    "header_l": 50.8,  # 20 pins at 2.54
    "header_w": 5.08,
    "header_h": 2.5,  # UNMEASURED: plastic body above the PCB
    "usb_w": 9.0,  # UNMEASURED
    "usb_d": 7.3,  # UNMEASURED
    "usb_h": 3.3,  # UNMEASURED
    "hdmi_w": 11.6,  # UNMEASURED
    "hdmi_d": 7.0,  # UNMEASURED
    "hdmi_h": 3.3,  # UNMEASURED
    "soc_l": 14,  # UNMEASURED
    "soc_h": 1.0,  # UNMEASURED
    "sink_y0": 8.7,  # ESTIMATE from the vendor product photo: the body starts below the header row
    "sink_y1": 32.0,  # ESTIMATE: ports poke out just past it
    "sink_top": 10,  # PLACEHOLDER: height above the PCB underside is not visible in the photo
    "ear_w": 5.7,  # ESTIMATE: screw ears either side of the header
    "ear_overlap": 0.5,  # the ears reach this far into the plate, so the heatsink is one solid, not three
    "fan_l": 20.4,  # ESTIMATE: fan and bracket at the HDMI end (photo IMG_8393, 2026-10-05)
    "fan_y0": 11.8,
    "fan_top": 10.4,  # PLACEHOLDER
    "groove_w": 1.2,  # ESTIMATE: fin grooves
    "groove_depth": 2.5,  # PLACEHOLDER
    "pin_w": 0.64,  # standard square pin
    "cell_w": 2.24,  # UNMEASURED: plastic cell per pin, 0.3 mm gap at 2.54 pitch
    "pin_up": 6,  # UNMEASURED: pin above the plastic body
    "under_h": 1.5,  # UNMEASURED: backside connectors
    "sd_h": 1.8,  # UNMEASURED: microSD socket below the PCB, from the underside photo
}


UP = {"normal": [0, 0, 1], "x_dir": [1, 0, 0]}
DOWN = {"normal": [0, 0, -1], "x_dir": [1, 0, 0]}  # local v runs toward -Y


def plane(z, side):
    return {"origin": [0, 0, z], **(UP if side == "up" else DOWN)}


features = [
    {"id": "pcbs", "type": "sketch", "plane": "XY", "rect": {"width": "=pcb_l", "height": "=pcb_w", "center": ["=pcb_l/2", "=pcb_w/2"]}},
    {"id": "pcb", "type": "extrude", "profile": "pcbs", "distance": "=pcb_t"},
]
holes = [("=hole_inset", "=hole_inset"), ("=pcb_l-hole_inset", "=hole_inset"),
         ("=hole_inset", "=pcb_w-hole_inset"), ("=pcb_l-hole_inset", "=pcb_w-hole_inset")]
features.append({
    "id": "holes", "type": "sketch", "plane": plane("=pcb_t", "up"),
    "entities": [{"id": f"h{i}", "kind": "circle", "c": list(c), "r": "=hole_d/2"} for i, c in enumerate(holes)],
})
features.append({
    "id": "pcbh", "type": "extrude", "base": "pcb", "distance": "=-pcb_t",
    "profiles": [{"sketch": "holes", "bounded_by": [f"h{i}"]} for i in range(4)],
})


def add(fid, side, z, cx, cy, width, depth, height, base=None):
    """A box on the top (z = surface, grows up) or under the board (z = 0, grows
    down). With no base it is a body of its own."""
    v = cy if side == "up" else f"=-({cy})" if isinstance(cy, str) else -cy
    extrude = {"id": fid, "type": "extrude", "profile": f"{fid}s", "distance": height}
    if base:
        extrude["base"] = base
    features.extend([
        {"id": f"{fid}s", "type": "sketch", "plane": plane(z, side), "rect": {"width": width, "height": depth, "center": [cx, v]}},
        extrude,
    ])


def add_posts(fid, z, length, size):
    """One square post per header position, 2 x 20 at 2.54 pitch: one post, then
    a pattern, which is a body of its own. The header is surface mount, so
    nothing passes through the board."""
    features.extend([
        {"id": f"{fid}0s", "type": "sketch", "plane": plane(z, "up"),
         "rect": {"width": size, "height": size, "center": ["=pcb_l/2-9.5*2.54", "=header_w/2-1.27"]}},
        {"id": f"{fid}0", "type": "extrude", "profile": f"{fid}0s", "distance": length},
        {"id": fid, "type": "pattern", "tool": f"{fid}0",
         "linear": {"direction": [1, 0, 0], "count": 20, "spacing": 2.54},
         "linear2": {"direction": [0, 1, 0], "count": 2, "spacing": 2.54}},
    ])


# Top side. x positions of the ports are pixel estimates, UNMEASURED.
add_posts("cells", "=pcb_t", "=header_h", "=cell_w")  # one plastic cell per pin, with a gap between
add_posts("pins", "=pcb_t+header_h", "=pin_up", "=pin_w")
add("usbc1", "up", "=pcb_t", 11.5, "=pcb_w-usb_d/2+0.5", "=usb_w", "=usb_d", "=usb_h")
add("usbc2", "up", "=pcb_t", 23.2, "=pcb_w-usb_d/2+0.5", "=usb_w", "=usb_d", "=usb_h")
add("hdmi", "up", "=pcb_t", 52.7, "=pcb_w-hdmi_d/2+0.5", "=hdmi_w", "=hdmi_d", "=hdmi_h")
add("soc", "up", "=pcb_t", 38, 18, "=soc_l", "=soc_l", "=soc_h")
SINK_Z = "=pcb_t+usb_h"  # the heatsink body rests on the tallest connector
add("pad", "up", "=pcb_t+soc_h", 38, 18, "=soc_l", "=soc_l", "=usb_h-soc_h")  # thermal pad up to the sink
# The heatsink is one body: the plate, two screw ears, fin grooves and screw holes.
add("sink", "up", SINK_Z, "=pcb_l/2", "=(sink_y0+sink_y1)/2", "=pcb_l", "=sink_y1-sink_y0", "=sink_top-pcb_t-usb_h")
add("earl", "up", "=pcb_t", "=ear_w/2", "=(sink_y0+ear_overlap)/2", "=ear_w", "=sink_y0+ear_overlap", "=usb_h+ear_overlap", base="sink")
add("earr", "up", "=pcb_t", "=pcb_l-ear_w/2", "=(sink_y0+ear_overlap)/2", "=ear_w", "=sink_y0+ear_overlap", "=usb_h+ear_overlap", base="earl")
add("fan", "up", SINK_Z, "=pcb_l-fan_l/2", "=(fan_y0+sink_y1+0.5)/2", "=fan_l", "=sink_y1+0.5-fan_y0", "=fan_top-pcb_t-usb_h")
# The fan bracket is a body of its own, so the heatsink gives up the space it sits in.
features.append({"id": "fancut", "type": "extrude", "profile": "fans", "base": "earr", "mode": "cut", "distance": "=fan_top-pcb_t-usb_h"})

# Fin grooves along X, cut from the sink top.
grooves = []
for k in range(7):
    y0 = f"=fan_y0+1.1+{k}*2.7"
    y1 = f"=fan_y0+1.1+{k}*2.7+groove_w"
    x0, x1 = "=-0.1", "=pcb_l-fan_l-0.5"
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    ids = [f"g{k}_{j}" for j in range(4)]
    for j in range(4):
        grooves.append({"id": ids[j], "kind": "line", "p0": list(corners[j]), "p1": list(corners[(j + 1) % 4])})
features.extend([
    {"id": "groovess", "type": "sketch", "plane": plane("=sink_top", "up"), "entities": grooves},
    {"id": "grooved", "type": "extrude", "base": "fancut", "distance": "=-groove_depth",
     "profiles": [{"sketch": "groovess", "bounded_by": [f"g{k}_{j}" for j in range(4)]} for k in range(7)]},
])

# Screw holes through the ears. The board has its own at the same places.
features.append({
    "id": "screws", "type": "hole", "base": "grooved", "diameter": "=hole_d", "through": True,
    "points": [["=hole_inset", "=hole_inset", "=pcb_t+usb_h"], ["=pcb_l-hole_inset", "=hole_inset", "=pcb_t+usb_h"]],
})

# Backside, from a photo of the real underside (2026-10-05, about 12.3 px/mm).
# Model coordinates, measured off the photo, not with calipers: all UNMEASURED.
def add_under(fid, x0, x1, y0, y1, height="=under_h"):
    add(fid, "down", 0, round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3), round(x1 - x0, 3), round(y1 - y0, 3), height)


add_under("sdcard", 53.0, 65.5, 11.1, 22.4, "=sd_h")  # overhangs the +X edge by 0.5; the card slides out toward +X
add_under("mipi", 28.9, 45.5, 26.8, 30.9)  # MIPI LCD FPC
add_under("cam1", 22.0, 36.0, 0.4, 6.1)
add_under("cam2", 37.8, 52.0, 0.4, 6.1)
add_under("fanc", 52.7, 57.3, 0.0, 3.7)  # fan connector
add_under("pcie", 0.4, 5.3, 13.0, 24.0)  # PCIe FPC

SILVER = "#b8bcc2"
BLACK = "#1b1c1e"
BEIGE = "#d9c9a3"
GPIO_BLUE = "#2aa1d8"

bodies = {
    "pcb": {"name": "PCB", "color": "#2d68b0"},
    "cells": {"name": "Header plastic", "color": GPIO_BLUE},
    "pins": {"name": "Header pins", "color": "#d4af37"},
    "usbc1": {"name": "USB-C 1", "color": SILVER},
    "usbc2": {"name": "USB-C 2", "color": SILVER},
    "hdmi": {"name": "Mini HDMI", "color": SILVER},
    "soc": {"name": "SoC", "color": BLACK},
    "pad": {"name": "Thermal pad", "color": "#8a8f96"},
    "sink": {"name": "Heatsink", "color": "#2a2c2f"},
    "fan": {"name": "Fan bracket", "color": SILVER},
    "sdcard": {"name": "microSD socket", "color": SILVER},
    "mipi": {"name": "MIPI LCD connector", "color": BEIGE},
    "cam1": {"name": "Camera connector 1", "color": BEIGE},
    "cam2": {"name": "Camera connector 2", "color": BEIGE},
    "fanc": {"name": "Fan connector", "color": BEIGE},
    "pcie": {"name": "PCIe connector", "color": BEIGE},
}


def paint(colour, x0, y0, z0, x1, y1, z1):
    """Colours every face whose centre lies in the box. Later entries win."""
    return {"color": colour, "select": {"select": [{"op": "faces"}, {"op": "within", "box": [x0, y0, z0, x1, y1, z1]}]}}


PIN_COLOURS = {1: "#e8c52e", 17: "#e8c52e", 2: "#d83a2e", 4: "#d83a2e", 27: "#2f9e44"}
PIN_COLOURS.update({n: "#1d1d1f" for n in (6, 9, 14, 20, 25, 30, 34, 39)})  # ground


def header_cells():
    """Header plastic coloured by pin function, as on the vendor photo: 3.3 V yellow,
    5 V red, ground black, ID_SD green. Everything else keeps the body's GPIO blue.
    Pin 1 is at the high-X (HDMI and fan) end on the inner row; pin 2 is beside it on the board-edge
    row. Face colours, because one body holds all 40 cells."""
    entries = []
    for number, colour in sorted(PIN_COLOURS.items()):
        x = 65 / 2 + (9.5 - (number - 1) // 2) * 2.54
        y = 1.27 if number % 2 == 0 else 3.81
        entries.append(paint(colour, x - 1.2, y - 1.2, 1.1, x + 1.2, y + 1.2, 3.75))
    return entries


appearances = header_cells()

document = {
    "format_version": 2, "features": features, "rollback_index": len(features),
    "parameters": parameters, "appearances": appearances, "bodies": bodies, "revision": 0, "log": [],
}
out = sys.argv[1] if len(sys.argv) > 1 else "examples/orangepi-zero-3w.sprocket"
json.dump(document, open(out, "w"), indent=1)
