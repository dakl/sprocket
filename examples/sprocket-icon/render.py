"""Renders a STEP file to a PNG with smooth shading and no outlines.
Run from engine/: uv run python ../examples/sprocket-icon/render.py part.step out.png [x,y,z camera direction]."""
import sys, numpy as np, zlib, struct
from pathlib import Path
from build123d import import_step

step_path, out_path = sys.argv[1], sys.argv[2]
SIZE, SS = 1024, 2
W = SIZE * SS
shape = import_step(step_path)

verts, norms, tris = [], [], []
offset = 0
for face in shape.faces():
    v, t = face.tessellate(0.01, 0.15)
    if not t:
        continue
    for p in v:
        n = face.normal_at(p)
        verts.append((p.X, p.Y, p.Z)); norms.append((n.X, n.Y, n.Z))
    tris += [(a + offset, b + offset, c + offset) for a, b, c in t]
    offset += len(v)
V, N, T = np.array(verts), np.array(norms), np.array(tris)
print("triangles", len(T))

eye = np.array([float(x) for x in sys.argv[3].split(',')]) if len(sys.argv) > 3 else np.array([0.45, -0.7, 0.9]); eye /= np.linalg.norm(eye)
right = np.cross([0, 0, 1], eye) if abs(eye[2]) < 0.999 else np.array([1.0, 0, 0]); right /= np.linalg.norm(right)
up = np.cross(eye, right)
centre = (V.min(0) + V.max(0)) / 2
L = V - centre
extent = np.abs(np.stack([L @ right, L @ up], 1)).max()
scale = 0.84 * W / (2 * extent)
sx = (L @ right) * scale + W / 2
sy = W / 2 - (L @ up) * scale + W * 0.01
sz = L @ eye

def unit(v): v = np.array(v, float); return v / np.linalg.norm(v)
key = unit(eye + right * -0.9 + up * 0.8)
fill = unit(eye + right * 1.2 + up * -0.2)
rim = unit(-eye + right * 0.3 + up * 0.9)
BRASS = np.array([0.86, 0.66, 0.32])

def env(r):
    """A fake studio: bright sky above, dark floor, a soft horizontal band."""
    h = r @ np.array([0, 0, 1.0])
    sky = np.array([1.0, 0.96, 0.88]); floor = np.array([0.10, 0.08, 0.07])
    t = np.clip(h * 0.5 + 0.5, 0, 1)[..., None]
    c = floor + (sky - floor) * (t ** 1.6)
    band = (np.exp(-((h - 0.15) / 0.12) ** 2) * 0.5)[..., None]
    return c + band

def shade(n):
    n = n / np.linalg.norm(n, axis=-1, keepdims=True)
    n = np.where((n @ eye)[..., None] < 0, -n, n)
    diff = (0.9 * np.clip(n @ key, 0, 1))[..., None] * np.array([1.0, 0.95, 0.85]) \
         + (0.35 * np.clip(n @ fill, 0, 1))[..., None] * np.array([0.7, 0.8, 1.0]) \
         + (0.25 * np.clip(n @ rim, 0, 1))[..., None]
    refl = 2 * (n @ eye)[..., None] * n - eye
    refl_c = env(refl)
    fres = (0.08 + 0.35 * (1 - np.clip(n @ eye, 0, 1)) ** 3)[..., None]
    half = unit(key + eye)
    spec = (np.clip(n @ half, 0, 1) ** 60 * 0.9)[..., None]
    base = BRASS * (0.16 + diff)
    return np.clip(base * (1 - fres) + BRASS * refl_c * fres * 1.6 + spec * np.array([1, 0.96, 0.9]), 0, 1)

yy, xx = np.mgrid[0:W, 0:W]
d = np.hypot((xx - W / 2) / W, (yy - W * 0.46) / W)
bg = np.array([0.075, 0.09, 0.13]) + (np.array([0.20, 0.23, 0.30]) - np.array([0.075, 0.09, 0.13])) * np.clip(1 - d * 1.5, 0, 1)[..., None] ** 2
img = bg.copy()
zbuf = np.full((W, W), -np.inf)
valid_mask = np.zeros((W, W), bool)


def raster(tri_xy, fn):
    (x0, y0), (x1, y1), (x2, y2) = tri_xy
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(den) < 1e-9:
        return
    xa, xb = int(max(min(x0, x1, x2) - 1, 0)), int(min(max(x0, x1, x2) + 2, W))
    ya, yb = int(max(min(y0, y1, y2) - 1, 0)), int(min(max(y0, y1, y2) + 2, W))
    if xa >= xb or ya >= yb:
        return
    px, py = np.meshgrid(np.arange(xa, xb) + 0.5, np.arange(ya, yb) + 0.5)
    l0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / den
    l1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / den
    l2 = 1 - l0 - l1
    inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    if inside.any():
        fn(xa, xb, ya, yb, inside, l0, l1, l2)

# ground shadow
shadow = np.zeros((W, W))
zmin = V[:, 2].min()
G = V.copy(); G[:, 2] = zmin - 0.4
GL = G - centre
gx = (GL @ right) * scale + W / 2
gy = W / 2 - (GL @ up) * scale + W * 0.01
def put_shadow(xa, xb, ya, yb, inside, *_):
    shadow[ya:yb, xa:xb][inside] = 1.0
for a, b, c in T:
    raster(((gx[a], gy[a]), (gx[b], gy[b]), (gx[c], gy[c])), put_shadow)
from scipy import ndimage
shadow = ndimage.gaussian_filter(shadow, 26 * SS / 2) * 0.55
img = bg * (1 - shadow[..., None])
zbuf = np.full((W, W), -np.inf)

for a, b, c in T:
    def paint(xa, xb, ya, yb, inside, l0, l1, l2):
        z = l0 * sz[a] + l1 * sz[b] + l2 * sz[c]
        region = zbuf[ya:yb, xa:xb]
        win = inside & (z > region)
        if not win.any():
            return
        n = l0[..., None] * N[a] + l1[..., None] * N[b] + l2[..., None] * N[c]
        col = shade(n[win])
        region[win] = z[win]
        img[ya:yb, xa:xb][win] = col
    raster(((sx[a], sy[a]), (sx[b], sy[b]), (sx[c], sy[c])), paint)

out = img.reshape(SIZE, SS, SIZE, SS, 3).mean(axis=(1, 3))
rgb = (np.clip(out, 0, 1) * 255).astype(np.uint8)
raw = b"".join(b"\x00" + rgb[r].tobytes() for r in range(SIZE))
def chunk(tag, data):
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
Path(out_path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
print("wrote", out_path)
