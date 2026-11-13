"""render_view: a PNG of the built part, from a named or given camera.

This is the agent's eyes. An agent cannot look at the viewport, so every check
it makes on its own work is either a number from `geometry.py` or this picture.

It is a z-buffer rasteriser over the same tessellation `mesh.py` writes, about
a hundred lines of numpy, rather than a plotting library. Three reasons.
Matplotlib's 3D axes sort whole polygons by depth, so a face can show through
another one, and an agent cannot tell a real hole from a sorting artefact.
Highlighting wants per-triangle colour keyed by B-rep face, which
`triangles_per_face` already gives and a plotting API does not. And PNG is
eleven lines of zlib, so neither the renderer nor the writer needs a
dependency that has to be built on every platform the engine runs on.

Orthographic, not perspective: a CAD view is measured against, and parallel
lines that stay parallel are worth more here than depth cues.
"""

import struct
import zlib
from pathlib import Path

import numpy as np

from .mesh import tessellate

# Camera direction is where the camera sits relative to the part, so "front"
# stands on -Y and looks toward +Y. Same convention as Fusion 360.
NAMED_CAMERAS = {
    "iso": (1.0, -1.0, 1.0),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
}

BACKGROUND = (255, 255, 255)
PART_COLOUR = (176, 186, 197)
HIGHLIGHT_COLOUR = (240, 122, 60)
EDGE_COLOUR = (48, 52, 58)
AMBIENT = 0.35

# The light sits off the camera axis by this much screen-right and screen-up.
# A light exactly at the camera gives every visible face of a box the same
# brightness: measured on the iso view, the top, front and right faces all
# shaded 0.577, so a box rendered as a flat hexagon. Offset by this the same
# three read 0.82, 0.49 and 0.78.
LIGHT_OFFSET_RIGHT = 0.35
LIGHT_OFFSET_UP = 0.25

# Edges lie exactly on the faces they bound, so an equal-depth test loses the
# coin toss on about half the pixels and the outline comes out dashed. This
# pulls an edge toward the camera by a fraction of the model's own depth range.
EDGE_DEPTH_BIAS = 2e-3

# The part fills this fraction of the shorter image side. Under 1 so a fillet
# on the silhouette is not clipped by a rounding error.
FIT = 0.88

# Rendered this many times larger, then averaged down. Without it every
# silhouette is a staircase, and an agent reads the staircase as a feature.
SUPERSAMPLE = 2


class RenderError(Exception):
    pass


def _camera_direction(camera) -> np.ndarray:
    if isinstance(camera, str):
        if camera not in NAMED_CAMERAS:
            raise RenderError(
                f"unknown camera {camera!r} (known: {', '.join(sorted(NAMED_CAMERAS))})"
            )
        direction = NAMED_CAMERAS[camera]
    elif isinstance(camera, (list, tuple)) and len(camera) == 3:
        direction = camera
    else:
        raise RenderError("camera must be a name or a three-number direction")

    vector = np.asarray(direction, dtype=np.float64)
    length = np.linalg.norm(vector)
    if not np.isfinite(length) or length < 1e-12:
        raise RenderError(f"camera direction {list(direction)} has no length")
    return vector / length


def _basis(eye: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Screen right and up for a camera looking down -eye.

    +Z is up unless the camera is looking straight down it, where "up" has no
    answer and the world's +Y is the convention every CAD tool uses instead.
    """
    world_up = np.array([0.0, 0.0, 1.0])
    if abs(float(eye @ world_up)) > 1.0 - 1e-9:
        world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(world_up, eye)
    right /= np.linalg.norm(right)
    return right, np.cross(eye, right)


def _light(eye: np.ndarray, right: np.ndarray, up: np.ndarray) -> np.ndarray:
    direction = eye + LIGHT_OFFSET_RIGHT * right + LIGHT_OFFSET_UP * up
    return direction / np.linalg.norm(direction)


def _face_of_triangle(triangles_per_face: np.ndarray) -> np.ndarray:
    """Face index per triangle, expanded from the per-face run lengths."""
    return np.repeat(np.arange(len(triangles_per_face)), triangles_per_face)


def _resolve_highlight(handles, face_count: int) -> set[int]:
    indices = set()
    for handle in handles or []:
        if isinstance(handle, int) and not isinstance(handle, bool):
            index = handle
        elif (
            isinstance(handle, str) and handle.startswith("f") and handle[1:].isdigit()
        ):
            index = int(handle[1:])
        else:
            raise RenderError(f"highlight {handle!r} must be a face handle like 'f2'")
        if not 0 <= index < face_count:
            raise RenderError(
                f"highlight 'f{index}' is out of range, this shape has {face_count} faces"
            )
        indices.add(index)
    return indices


def render_png(
    shape,
    path: Path,
    camera="iso",
    width: int = 640,
    height: int = 480,
    highlight: list | None = None,
) -> dict:
    """Renders `shape` to a PNG and returns what it drew.

    The returned dict is the part a test asserts on. A picture cannot be
    compared for correctness, but "this many triangles, this projected size,
    these faces highlighted" can.
    """
    if width < 16 or height < 16 or width > 4096 or height > 4096:
        raise RenderError("width and height must be between 16 and 4096")

    instance = tessellate(shape)
    vertices = np.asarray(instance["vertices"], dtype=np.float64).reshape(-1, 3)
    triangles = np.asarray(instance["triangles"], dtype=np.int64).reshape(-1, 3)
    per_face = np.asarray(instance["triangles_per_face"], dtype=np.int64)
    segments = np.asarray(instance["edges"], dtype=np.float64).reshape(-1, 2, 3)
    if len(triangles) == 0:
        raise RenderError("nothing to render: the tessellation has no triangles")

    highlighted = _resolve_highlight(highlight, len(per_face))
    triangle_is_highlighted = np.isin(_face_of_triangle(per_face), list(highlighted))

    eye = _camera_direction(camera)
    right, up = _basis(eye)
    light = _light(eye, right, up)

    centre = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    local = vertices - centre
    view = np.stack([local @ right, local @ up, local @ eye], axis=1)

    big_width, big_height = width * SUPERSAMPLE, height * SUPERSAMPLE
    extent = np.abs(view[:, :2]).max()
    if extent < 1e-12:
        raise RenderError("the shape projects to a single point from this camera")
    scale = FIT * min(big_width, big_height) / (2.0 * extent)

    def project(points: np.ndarray) -> np.ndarray:
        """World xyz to screen x, screen y, camera-space depth."""
        local = points - centre
        return np.stack(
            [
                (local @ right) * scale + big_width / 2.0,
                big_height / 2.0 - (local @ up) * scale,
                local @ eye,
            ],
            axis=-1,
        )

    screen = project(vertices)
    screen_x, screen_y, depth = screen[:, 0], screen[:, 1], screen[:, 2]

    colour = np.zeros((big_height, big_width, 3), dtype=np.float64)
    colour[:] = BACKGROUND
    z_buffer = np.full((big_height, big_width), -np.inf)

    for index, (a, b, c) in enumerate(triangles):
        base = HIGHLIGHT_COLOUR if triangle_is_highlighted[index] else PART_COLOUR
        _raster_triangle(
            colour,
            z_buffer,
            np.array([screen_x[a], screen_x[b], screen_x[c]]),
            np.array([screen_y[a], screen_y[b], screen_y[c]]),
            np.array([depth[a], depth[b], depth[c]]),
            _shade(vertices[a], vertices[b], vertices[c], light),
            base,
        )

    # Drawn after the faces so an edge always wins its own pixel. Without the
    # outline a fillet and a chamfer render as the same smooth grey lump, and
    # telling those apart is most of what an agent looks at a render for.
    bias = EDGE_DEPTH_BIAS * max(float(depth.max() - depth.min()), 1.0)
    for segment in project(segments.reshape(-1, 3)).reshape(-1, 2, 3):
        _raster_segment(colour, z_buffer, segment, bias)

    image = colour.reshape(height, SUPERSAMPLE, width, SUPERSAMPLE, 3).mean(axis=(1, 3))
    _write_png(Path(path), np.clip(image, 0, 255).astype(np.uint8))
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "camera": camera if isinstance(camera, str) else list(camera),
        "triangles": len(triangles),
        "faces": len(per_face),
        "edges": len(instance["segments_per_edge"]),
        "highlighted": sorted(f"f{index}" for index in highlighted),
    }


def _shade(a, b, c, light: np.ndarray) -> float:
    """Flat lambert from a light just off the camera axis.

    The normal comes from the triangle's own winding, not the tessellator's
    per-vertex normals, so a triangle always shades as the facet it is. Taken
    absolute because a camera inside a shell sees back faces, and a black hole
    in the middle of a part reads as geometry that is not there.
    """
    normal = np.cross(b - a, c - a)
    length = np.linalg.norm(normal)
    if length < 1e-15:
        return AMBIENT
    return AMBIENT + (1.0 - AMBIENT) * abs(float((normal / length) @ light))


def _raster_triangle(colour, z_buffer, xs, ys, zs, shade: float, base) -> None:
    """One triangle, depth-tested per pixel.

    Barycentric rather than scanline: the same three weights give both the
    inside test and the interpolated depth, and it stays correct for a
    triangle of any orientation without a special case per edge.
    """
    height, width = z_buffer.shape
    min_x = max(int(np.floor(xs.min())), 0)
    max_x = min(int(np.ceil(xs.max())), width - 1)
    min_y = max(int(np.floor(ys.min())), 0)
    max_y = min(int(np.ceil(ys.max())), height - 1)
    if min_x > max_x or min_y > max_y:
        return

    area = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
    if abs(area) < 1e-12:  # edge-on, contributes nothing this camera can see
        return

    grid_y, grid_x = np.mgrid[min_y : max_y + 1, min_x : max_x + 1]
    px = grid_x + 0.5
    py = grid_y + 0.5
    w0 = ((xs[1] - px) * (ys[2] - py) - (xs[2] - px) * (ys[1] - py)) / area
    w1 = ((xs[2] - px) * (ys[0] - py) - (xs[0] - px) * (ys[2] - py)) / area
    w2 = 1.0 - w0 - w1

    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not inside.any():
        return

    pixel_depth = w0 * zs[0] + w1 * zs[1] + w2 * zs[2]
    window = z_buffer[min_y : max_y + 1, min_x : max_x + 1]
    visible = inside & (pixel_depth > window)
    if not visible.any():
        return

    window[visible] = pixel_depth[visible]
    colour[min_y : max_y + 1, min_x : max_x + 1][visible] = np.asarray(base) * shade


def _raster_segment(colour, z_buffer, segment: np.ndarray, bias: float) -> None:
    """One B-rep edge segment, depth-tested against the faces already drawn.

    Point sampling along the line rather than a proper line algorithm: one
    sample per pixel of the longer screen axis leaves no gaps, and supersampling
    turns the resulting hard pixels into a smooth line on the way down.
    """
    height, width = z_buffer.shape
    start, end = segment
    span = int(np.ceil(max(abs(end[0] - start[0]), abs(end[1] - start[1])))) + 1
    steps = np.linspace(0.0, 1.0, span)
    points = start + steps[:, None] * (end - start)

    x = np.round(points[:, 0]).astype(np.int64)
    y = np.round(points[:, 1]).astype(np.int64)
    on_screen = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    if not on_screen.any():
        return

    x, y, z = x[on_screen], y[on_screen], points[on_screen, 2] + bias
    visible = z > z_buffer[y, x]
    if not visible.any():
        return
    z_buffer[y[visible], x[visible]] = z[visible]
    colour[y[visible], x[visible]] = EDGE_COLOUR


def _write_png(path: Path, image: np.ndarray) -> None:
    """Minimal 8-bit RGB PNG. Filter type 0 on every row, one zlib stream."""
    height, width, _ = image.shape
    raw = b"".join(b"\0" + image[row].tobytes() for row in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
