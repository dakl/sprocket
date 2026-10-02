"""Writes sprocket.sprocket in the current directory: a 30 mm, 13-tooth ANSI 25 roller-chain sprocket.
Run: python gen.py [fillet_radius_mm]. The committed examples/sprocket.sprocket is its output at 0.3."""
import json, math
N, PITCH, ROLLER, TIP_R, THICK = 13, 6.35, 3.3, 15.0, 3.0
HUB_R, HUB_H, BORE_R = 4.8, 5.5, 2.5
HOLES, HOLE_R, HOLE_AT = 5, 2.0, 8.2
CAP_R = 0.55
import sys
FILLET = float(sys.argv[1]) if len(sys.argv) > 1 else 0.3
seat_r = ROLLER / 2 + 0.05
pitch_r = PITCH / (2 * math.sin(math.pi / N))
step = math.tau / N
EXT = math.radians(-5)
entities = []
def pol(centre, angle, radius):
    return [round(centre[0] + radius * math.cos(angle), 6), round(centre[1] + radius * math.sin(angle), 6)]
seat_end = {}
for k in range(N):
    a = k * step
    centre = (pitch_r * math.cos(a), pitch_r * math.sin(a))
    mid = a + step / 2
    cap_centre = ((TIP_R - CAP_R) * math.cos(mid), (TIP_R - CAP_R) * math.sin(mid))
    entities.append({"id": f"seat{k}", "kind": "arc", "c": [round(centre[0], 6), round(centre[1], 6)], "r": seat_r,
                     "a0": a + math.radians(90) - EXT, "a1": a + math.radians(270) + EXT})
    entities.append({"id": f"cap{k}", "kind": "arc", "c": [round(cap_centre[0], 6), round(cap_centre[1], 6)], "r": CAP_R,
                     "a0": mid - math.pi / 2, "a1": mid + math.pi / 2})
for k in range(N):
    nxt = (k + 1) % N
    seat = entities[2 * k]; cap = entities[2 * k + 1]; seat_next = entities[2 * nxt]
    a = k * step
    seat_hi = pol(seat["c"], seat["a0"], seat_r)
    cap_lo = pol(cap["c"], cap["a0"], CAP_R)
    cap_hi = pol(cap["c"], cap["a1"], CAP_R)
    seat_next_lo = pol(seat_next["c"], seat_next["a1"], seat_r)
    entities.append({"id": f"flank_up{k}", "kind": "line", "p0": seat_hi, "p1": cap_lo})
    entities.append({"id": f"flank_dn{k}", "kind": "line", "p0": cap_hi, "p1": seat_next_lo})

features = [
    {"id": "plate_sk", "type": "sketch", "plane": "XY", "entities": entities},
    {"id": "plate", "type": "extrude", "profile": "plate_sk", "distance": THICK},
    {"id": "hub_sk", "type": "sketch", "plane": "XY", "circle": {"radius": HUB_R}},
    {"id": "hub", "type": "extrude", "profile": "hub_sk", "distance": HUB_H},
    {"id": "body", "type": "boolean", "operation": "union", "base": "plate", "tool": "hub"},
]
previous = "body"
for i in range(HOLES):
    a = math.tau * i / HOLES + math.pi / 2
    features += [
        {"id": f"hole{i}_sk", "type": "sketch", "plane": "XY",
         "circle": {"radius": HOLE_R, "center": [round(HOLE_AT * math.cos(a), 5), round(HOLE_AT * math.sin(a), 5)]}},
        {"id": f"hole{i}", "type": "extrude", "profile": f"hole{i}_sk", "distance": THICK},
        {"id": f"cut{i}", "type": "boolean", "operation": "subtract", "base": previous, "tool": f"hole{i}"},
    ]
    previous = f"cut{i}"
features += [
    {"id": "bore_sk", "type": "sketch", "plane": "XY", "circle": {"radius": BORE_R}},
    {"id": "bore", "type": "extrude", "profile": "bore_sk", "distance": HUB_H},
    {"id": "cut_final", "type": "boolean", "operation": "subtract", "base": previous, "tool": "bore"},
    {"id": "sprocket", "type": "fillet", "base": "cut_final", "edges": {"from": "part", "select": [{"op": "edges"}]}, "radius": FILLET},
]
json.dump({"schema_version": 1, "units": {"length": "mm"}, "features": features}, open("sprocket.sprocket", "w"), indent=1)
print(len(entities), "entities,", len(features), "features")
