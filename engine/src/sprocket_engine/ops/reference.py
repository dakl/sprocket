from pathlib import Path

from build123d import Axis, Part, Pos, import_step

from .registry import register

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}

SCHEMA = {
    "type": "object",
    "description": (
        "Brings in another .sprocket document or a STEP file as a reference body: shown translucent, not "
        "pickable and never exported. Use it to design around a part, as a boolean `tool`, or with "
        "check_clearance. A relative `file` is relative to this document's folder. The referenced file is read "
        "again when it changes."
    ),
    "required": ["id", "type", "file"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "reference"},
        "file": {
            "type": "string",
            "description": "path to a .sprocket, .step or .stp file",
        },
        "rotate": {
            **VECTOR3,
            "description": "degrees about world X, then Y, then Z, about the origin",
        },
        "translate": {**VECTOR3, "description": "mm, applied after rotate"},
    },
}

# Documents being read right now, so a document that includes itself fails
# with a message instead of recursing until the stack runs out.
_loading: set[Path] = set()


def _load_document(path: Path):
    from ..document import Document
    from ..replay import replay

    if path in _loading:
        raise ValueError(f"reference cycle: {path.name} includes itself")
    _loading.add(path)
    try:
        part = replay(Document.load(path).for_replay())
    finally:
        _loading.discard(path)
    if part is None or not part.solids():
        raise ValueError(f"referenced document {path.name} builds no solid")
    return part


@register("reference", SCHEMA)
def apply(feature: dict, ctx):
    path = Path(feature["file"]).resolve()
    if not path.is_file():
        raise ValueError(f"reference file {feature['file']} does not exist")
    if path.suffix == ".sprocket":
        shape = _load_document(path)
    elif path.suffix.lower() in (".step", ".stp"):
        shape = import_step(path)
    else:
        raise ValueError(
            f"a reference must be a .sprocket or STEP file, not {path.suffix or 'a file with no suffix'}"
        )
    # One compound of solids, which the mesh tessellates as one body.
    shape = Part(shape.solids())
    for axis, angle in zip((Axis.X, Axis.Y, Axis.Z), feature.get("rotate", (0, 0, 0))):
        if angle:
            shape = shape.rotate(axis, angle)
    if any(feature.get("translate", (0, 0, 0))):
        shape = Pos(*feature["translate"]) * shape
    return shape
