"""Turns what the viewer posts into document edits.

The viewer speaks in gestures: "extrude this face", "roll back to here". The
Swift app turns each one into a rewrite of the document file
(ContentView.swift). The browser has no such shell, so `sprocket web` does the
same translation here, on top of the daemon's edit API. Both sides read the
same rules from the viewer, so a change to a rule has to land in both.

Every command returns a list of edits for `Document.apply_edits`. Nothing here
touches the document, which keeps each rule testable on its own.
"""

from .document import Document, DocumentError

# The commands that change the document. `undo` and `redo` are handled by the
# caller, because they replace the document instead of editing it.
EDIT_COMMANDS = (
    "set_rollback",
    "extrude_face",
    "add_sketch",
    "extrude_sketch",
    "update_sketch",
    "update_feature",
    "new_document",
)


def edits_for(document: Document, message: dict) -> list[dict]:
    kind = message.get("type")
    if kind not in EDIT_COMMANDS:
        raise DocumentError(f"unknown viewer command {kind!r} (known: {', '.join(EDIT_COMMANDS)}, undo, redo)")
    return _COMMANDS[kind](document, message)


def _require(message: dict, *keys: str) -> None:
    missing = [key for key in keys if key not in message]
    if missing:
        raise DocumentError(f"{message.get('type')} needs {', '.join(missing)}")


def _new_id(kept: list[dict]) -> str:
    """`f<n>`, counting on from the features that will remain, and skipping any id in use."""
    used = {feature.get("id") for feature in kept}
    number = len(kept) + 1
    while f"f{number}" in used:
        number += 1
    return f"f{number}"


def _drop_rolled_back_tail(document: Document) -> tuple[list[dict], list[dict]]:
    """Deletes for the features past the rollback bar, and the features that stay.

    Building on top of a rolled-back part discards what was rolled back: replay
    only ever runs a prefix, so a hidden tail cannot sit in front of a live
    feature. Deleting from the end keeps every delete free of dependants.
    """
    kept = document.features[: document.rollback_index]
    deletes = [
        {"op": "delete", "id": feature["id"]} for feature in reversed(document.features[len(kept):])
    ]
    return deletes, kept


def _set_rollback(document: Document, message: dict) -> list[dict]:
    _require(message, "index")
    index = message["index"]
    if isinstance(index, bool) or not isinstance(index, int):
        raise DocumentError("'index' must be an integer")
    return [{"op": "set_rollback", "index": max(0, min(index, len(document.features)))}]


def _extrude_face(document: Document, message: dict) -> list[dict]:
    _require(message, "point", "normal", "distance")
    deletes, kept = _drop_rolled_back_tail(document)
    if not kept:
        raise DocumentError("there is no part to push or pull yet")
    feature = {
        "id": _new_id(kept),
        "type": "extrude_face",
        "base": kept[-1]["id"],
        "face": {
            "from": "part",
            "select": [
                {"op": "faces"},
                {"op": "nearest_to", "point": message["point"], "normal": message["normal"]},
            ],
        },
        "distance": message["distance"],
    }
    return deletes + [{"op": "insert", "feature": feature}]


def _add_sketch(document: Document, message: dict) -> list[dict]:
    _require(message, "plane", "polygon")
    deletes, kept = _drop_rolled_back_tail(document)
    feature = {
        "id": _new_id(kept),
        "type": "sketch",
        "plane": message["plane"],
        "polygon": message["polygon"],
    }
    return deletes + [{"op": "insert", "feature": feature}]


def _last_solid_id(features: list[dict]) -> str | None:
    """The newest feature that is not a sketch: what a new extrude should fuse into.

    A sketch makes no solid, so the part as it stands is whatever came last
    before it. Searching back from the sketch instead would name a solid that
    later features have already consumed.
    """
    for feature in reversed(features):
        if feature.get("type") != "sketch":
            return feature["id"]
    return None


def _extrude_sketch(document: Document, message: dict) -> list[dict]:
    _require(message, "profile", "distance")
    deletes, kept = _drop_rolled_back_tail(document)
    if not any(feature["id"] == message["profile"] for feature in kept):
        raise DocumentError(f"unknown sketch id {message['profile']!r}")
    feature = {
        "id": _new_id(kept),
        "type": "extrude",
        "profile": message["profile"],
        "distance": message["distance"],
    }
    base = _last_solid_id(kept)
    if base is not None:
        feature["base"] = base
    return deletes + [{"op": "insert", "feature": feature}]


def _update_sketch(document: Document, message: dict) -> list[dict]:
    _require(message, "id", "polygon")
    feature = dict(document.features[document.index_of(message["id"])])
    if feature.get("type") != "sketch":
        raise DocumentError(f"no sketch with id {message['id']!r}")
    # A sketch carries exactly one of rect, polygon and circle. Dragging a
    # corner turns any of them into a polygon.
    feature.pop("rect", None)
    feature.pop("circle", None)
    feature["polygon"] = message["polygon"]
    return [{"op": "update", "id": message["id"], "feature": feature}]


def _update_feature(document: Document, message: dict) -> list[dict]:
    _require(message, "id", "feature")
    return [{"op": "update", "id": message["id"], "feature": message["feature"]}]


def _new_document(document: Document, message: dict) -> list[dict]:
    edits = [{"op": "delete", "id": feature["id"]} for feature in reversed(document.features)]
    return edits + [{"op": "set_rollback", "index": 0}]


_COMMANDS = {
    "set_rollback": _set_rollback,
    "extrude_face": _extrude_face,
    "add_sketch": _add_sketch,
    "extrude_sketch": _extrude_sketch,
    "update_sketch": _update_sketch,
    "update_feature": _update_feature,
    "new_document": _new_document,
}
