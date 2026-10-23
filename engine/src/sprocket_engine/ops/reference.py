from collections.abc import Callable
from pathlib import Path

from build123d import Axis, Compound, Part, Pos

from sprocket_engine.b3d import import_step
from sprocket_engine.context import Context
from sprocket_engine.model import JSONObject, ReferenceFeature
from sprocket_engine.ops.registry import register

VECTOR3: JSONObject = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}

SCHEMA: JSONObject = {
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

type DocumentLoader = Callable[[Path], Compound]

# How to build a .sprocket file into one shape. `document.py` fills this in
# when it is imported: it imports the op registry, and so this module, so this
# module cannot import it back.
_document_loader: list[DocumentLoader] = []


def set_document_loader(loader: DocumentLoader) -> None:
    """Set how a referenced .sprocket file is built."""
    _document_loader[:] = [loader]


def _load_document(path: Path) -> Compound:
    if not _document_loader:
        msg = "a .sprocket reference needs sprocket_engine.document imported"
        raise ValueError(msg)
    return _document_loader[0](path)


@register("reference", SCHEMA)
def apply(feature: ReferenceFeature, _ctx: Context) -> Compound:
    """Load the referenced document or STEP file as one body, placed."""
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
    rotate = feature.get("rotate", [0, 0, 0])
    for axis, angle in zip((Axis.X, Axis.Y, Axis.Z), rotate, strict=False):
        if angle:
            shape = shape.rotate(axis, angle)
    translate = feature.get("translate", [0, 0, 0])
    if any(translate):
        shape = Pos(translate[0], translate[1], translate[2]) * shape
    return shape
