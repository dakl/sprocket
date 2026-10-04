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
    "fan_l": 20.4,  # ESTIMATE: fan and bracket at the left end
    "fan_y0": 11.8,
    "fan_top": 10.4,  # PLACEHOLDER
    "groove_w": 1.2,  # ESTIMATE: fin grooves
    "groove_depth": 2.5,  # PLACEHOLDER
    "pin_w": 0.64,  # standard square pin
    "cell_w": 2.24,  # UNMEASURED: plastic cell per pin, 0.3 mm gap at 2.54 pitch
    "pin_up": 6,  # UNMEASURED: pin above the plastic body
    "under_h": 1.5,  # UNMEASURED: backside connectors
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
previous = "pcbh"


def add(fid, side, z, cx, cy, width, depth, height):
    """A box on the top (z = surface, grows up) or under the board (z = 0, grows down)."""
    global previous
    v = cy if side == "up" else f"=-({cy})" if isinstance(cy, str) else -cy
    if isinstance(cy, str) and side == "up":
        v = cy
    features.extend([
        {"id": f"{fid}s", "type": "sketch", "plane": plane(z, side), "rect": {"width": width, "height": depth, "center": [cx, v]}},
        {"id": fid, "type": "extrude", "profile": f"{fid}s", "base": previous, "distance": height},
    ])
    previous = fid


def add_posts(fid, z, length, size):
    """One square post per header position, 2 x 20 at 2.54 pitch: one post,
    then a pattern. The header is surface mount, so nothing passes through the board."""
    global previous
    features.extend([
        {"id": f"{fid}0s", "type": "sketch", "plane": plane(z, "up"),
         "rect": {"width": size, "height": size, "center": ["=pcb_l/2-9.5*2.54", "=header_w/2-1.27"]}},
        {"id": f"{fid}0", "type": "extrude", "profile": f"{fid}0s", "distance": length},
        {"id": fid, "type": "pattern", "tool": f"{fid}0", "base": previous,
         "linear": {"direction": [1, 0, 0], "count": 20, "spacing": 2.54},
         "linear2": {"direction": [0, 1, 0], "count": 2, "spacing": 2.54}},
    ])
    previous = fid


# Top side. x positions of the ports are pixel estimates, UNMEASURED.
add_posts("cells", "=pcb_t", "=header_h", "=cell_w")  # one plastic cell per pin, with a gap between
add_posts("pins", "=pcb_t+header_h", "=pin_up", "=pin_w")
add("usbc1", "up", "=pcb_t", 11.5, "=pcb_w-usb_d/2+0.5", "=usb_w", "=usb_d", "=usb_h")
add("usbc2", "up", "=pcb_t", 23.2, "=pcb_w-usb_d/2+0.5", "=usb_w", "=usb_d", "=usb_h")
add("hdmi", "up", "=pcb_t", 52.7, "=pcb_w-hdmi_d/2+0.5", "=hdmi_w", "=hdmi_d", "=hdmi_h")
add("soc", "up", "=pcb_t", 38, 18, "=soc_l", "=soc_l", "=soc_h")
SINK_Z = "=pcb_t+usb_h"  # the heatsink body rests on the tallest connector
add("pad", "up", "=pcb_t+soc_h", 38, 18, "=soc_l", "=soc_l", "=usb_h-soc_h")  # thermal pad up to the sink
add("sink", "up", SINK_Z, "=pcb_l/2", "=(sink_y0+sink_y1)/2", "=pcb_l", "=sink_y1-sink_y0", "=sink_top-pcb_t-usb_h")
add("earl", "up", "=pcb_t", "=ear_w/2", "=sink_y0/2", "=ear_w", "=sink_y0", "=usb_h")
add("earr", "up", "=pcb_t", "=pcb_l-ear_w/2", "=sink_y0/2", "=ear_w", "=sink_y0", "=usb_h")
add("fan", "up", SINK_Z, "=fan_l/2", "=(fan_y0+sink_y1+0.5)/2", "=fan_l", "=sink_y1+0.5-fan_y0", "=fan_top-pcb_t-usb_h")

# Fin grooves along X, cut from the sink top.
grooves = []
for k in range(7):
    y0 = f"=fan_y0+1.1+{k}*2.7"
    y1 = f"=fan_y0+1.1+{k}*2.7+groove_w"
    x0, x1 = "=fan_l+0.5", "=pcb_l+0.1"
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    ids = [f"g{k}_{j}" for j in range(4)]
    for j in range(4):
        grooves.append({"id": ids[j], "kind": "line", "p0": list(corners[j]), "p1": list(corners[(j + 1) % 4])})
features.extend([
    {"id": "groovess", "type": "sketch", "plane": plane("=sink_top", "up"), "entities": grooves},
    {"id": "grooved", "type": "extrude", "base": previous, "distance": "=-groove_depth",
     "profiles": [{"sketch": "groovess", "bounded_by": [f"g{k}_{j}" for j in range(4)]} for k in range(7)]},
])
previous = "grooved"

# Screw holes through the ears and the board.
screws = [("=hole_inset", "=hole_inset"), ("=pcb_l-hole_inset", "=hole_inset")]
features.extend([
    {"id": "screwss", "type": "sketch", "plane": plane("=pcb_t+usb_h", "up"),
     "entities": [{"id": f"s{i}", "kind": "circle", "c": list(c), "r": "=hole_d/2"} for i, c in enumerate(screws)]},
    {"id": "screws", "type": "extrude", "base": previous, "distance": "=-(pcb_t+usb_h)",
     "profiles": [{"sketch": "screwss", "bounded_by": [f"s{i}"] } for i in range(2)]},
])
previous = "screws"

# Backside, from the vendor bottom-view picture. All UNMEASURED.
add("sdcard", "down", 0, 5.5, 18.5, 12, 15, "=under_h")
add("mipi", "down", 0, 27, 29.5, 17, 3, "=under_h")
add("cam1", "down", 0, 18, 2.5, 15.5, 4, "=under_h")
add("cam2", "down", 0, 34.5, 2.5, 15.5, 4, "=under_h")
add("fanc", "down", 0, 7, 2.5, 6, 4, "=under_h")  # fan connector
add("pcie", "down", 0, 59.5, 21, 3, 11, "=under_h")

SILVER = "#b8bcc2"
BLACK = "#1b1c1e"


def paint(colour, x0, y0, z0, x1, y1, z1):
    """Colours every face whose centre lies in the box. Later entries win."""
    return {"color": colour, "select": {"select": [{"op": "faces"}, {"op": "within", "box": [x0, y0, z0, x1, y1, z1]}]}}


PIN_COLOURS = {1: "#e8c52e", 17: "#e8c52e", 2: "#d83a2e", 4: "#d83a2e", 27: "#2f9e44"}
PIN_COLOURS.update({n: "#1d1d1f" for n in (6, 9, 14, 20, 25, 30, 34, 39)})  # ground
GPIO_BLUE = "#2aa1d8"


def header_cells():
    """Header plastic coloured by pin function, as on the vendor photo: 3.3 V yellow,
    5 V red, ground black, ID_SD green, everything else blue. Pin 1 is at the low-X
    end on the inner row; pin 2 is beside it on the board-edge row."""
    entries = []
    for number in range(1, 41):
        x = 65 / 2 + ((number - 1) // 2 - 9.5) * 2.54
        y = 1.27 if number % 2 == 0 else 3.81
        entries.append(paint(PIN_COLOURS.get(number, GPIO_BLUE), x - 1.2, y - 1.2, 1.1, x + 1.2, y + 1.2, 3.75))
    return entries


appearances = [
    {"color": "#2d68b0", "select": {"select": [{"op": "faces"}]}},  # PCB
    paint("#2a2c2f", -0.1, -0.1, 2.0, 65.1, 32.1, 10.2),  # heatsink and ears, black anodised
    paint(SILVER, -0.1, 11.7, 4.5, 20.5, 32.6, 10.5),  # fan bracket
    paint("#8a8f96", 30.9, 10.9, 2.3, 45.1, 26.1, 4.5),  # thermal pad
    *header_cells(),
    paint("#d4af37", 6, -0.1, 3.75, 59, 6, 9.8),  # header pins
    paint(SILVER, 7, 25, 1.2, 28, 32.6, 4.6),  # USB-C x2
    paint(SILVER, 46.5, 25, 1.2, 59, 32.6, 4.6),  # mini HDMI
    paint(BLACK, 30.9, 10.9, 1.2, 45.1, 26.1, 2.3),  # SoC
    paint(SILVER, -1, 10, -1.6, 13, 27, 0),  # microSD socket
    paint("#d9c9a3", 18, 27.9, -1.6, 36, 31.1, 0.1),  # MIPI LCD connector
    paint("#d9c9a3", 3, -0.1, -1.6, 43, 4.1, 0.1),  # camera and fan connectors
    paint("#d9c9a3", 57.5, 15.4, -1.6, 61, 26.6, 0.1),  # PCIe connector
]

document = {
    "format_version": 2, "features": features, "rollback_index": len(features),
    "parameters": parameters, "appearances": appearances, "revision": 0, "log": [],
}
out = sys.argv[1] if len(sys.argv) > 1 else "examples/orangepi-zero-3w.sprocket"
json.dump(document, open(out, "w"), indent=1)
