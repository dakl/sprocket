"""Edits inside one sketch: add, change or remove a constraint or an entity.

These are not timeline ops. One timeline row per constraint would bury the
features, so a sketch stays one feature and an `edit_sketch` edit changes it in
place. The viewer and an agent send the same edits, and the engine solves
after each one (solver.py), so every edit returns geometry that already obeys
the constraints, whoever made it.

Constraints are addressed by id (`c1`), as entities are (`e1`). An index shifts
when two clients edit at once, and an id does not.
"""

import copy
from collections.abc import Callable

from sprocket_engine.model import (
    JSON,
    JSONObject,
    entities_json,
    json_objects,
    sketch_feature,
)
from sprocket_engine.ops.sketch import CONSTRAINT_FIELDS, check_entities
from sprocket_engine.sketch_reference import (
    Project,
    has_references,
    parse_reference,
    projected_json,
    with_references_updated,
)

ENTITY_KINDS = ("line", "arc", "circle")
# What an update may not change: the id is the address, and a different kind
# is a different shape, which is a remove and an add.
FIXED_KEYS = ("id", "kind", "reference")

type Action = Callable[[JSONObject, JSONObject, Project | None], JSONObject]


def next_id(items: list[JSONObject], prefix: str) -> str:
    """`<prefix><n>`, one past the highest in use, so a deleted id does not come back on another shape."""
    used = [
        int(item_id[len(prefix) :])
        for item in items
        if isinstance(item_id := item.get("id", ""), str)
        and item_id.startswith(prefix)
        and item_id[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(used, default=0) + 1}"


def _list(feature: JSONObject, key: str) -> list[JSONObject]:
    """The feature's `key` list. Its items are the feature's own objects, so
    changing one changes the feature; a list that gains or loses an item is
    written back with `_put`."""
    return json_objects(feature.get(key, []), repr(key))


def _put(feature: JSONObject, key: str, items: list[JSONObject]) -> None:
    feature[key] = [*items]


def assign_constraint_ids(feature: JSONObject) -> None:
    """Gives every constraint without an id one. Documents from before M3.8 have none."""
    constraints = json_objects(feature.get("constraints", []), "'constraints'")
    for constraint in constraints:
        if "id" not in constraint:
            constraint["id"] = next_id(constraints, "c")


def _index_of(items: list[JSONObject], item_id: JSON, what: str) -> int:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            return index
    raise ValueError(f"no {what} {item_id!r}")


def _changes(edit: JSONObject) -> JSONObject:
    changes = edit.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("'changes' must be an object with at least one field")
    forbidden = [key for key in FIXED_KEYS if key in changes]
    if forbidden:
        raise ValueError(f"'changes' cannot set {', '.join(forbidden)}")
    return changes


def _object(edit: JSONObject, key: str) -> JSONObject:
    value = edit.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"'{key}' must be an object")
    return dict(value)


def _add_constraint(
    feature: JSONObject, edit: JSONObject, _project: Project | None = None
) -> JSONObject:
    constraint = _object(edit, "constraint")
    kind = constraint.get("kind")
    if not isinstance(kind, str) or kind not in CONSTRAINT_FIELDS:
        raise ValueError(
            f"constraint kind {kind!r} is not one of {', '.join(sorted(CONSTRAINT_FIELDS))}"
        )
    constraints = _list(feature, "constraints")
    _ = constraint.setdefault("id", next_id(constraints, "c"))
    _put(feature, "constraints", [*constraints, constraint])
    return {"constraint_id": constraint["id"]}


def _update_constraint(
    feature: JSONObject, edit: JSONObject, _project: Project | None = None
) -> JSONObject:
    constraints = _list(feature, "constraints")
    constraint = constraints[
        _index_of(constraints, edit.get("constraint_id"), "constraint")
    ]
    constraint.update(_changes(edit))
    return {"constraint_id": constraint["id"]}


def _remove_constraint(
    feature: JSONObject, edit: JSONObject, _project: Project | None = None
) -> JSONObject:
    constraints = _list(feature, "constraints")
    del constraints[_index_of(constraints, edit.get("constraint_id"), "constraint")]
    _put(feature, "constraints", constraints)
    return {"constraint_id": edit["constraint_id"]}


def _add_entity(
    feature: JSONObject, edit: JSONObject, _project: Project | None = None
) -> JSONObject:
    entity = _object(edit, "entity")
    if "reference" in entity:
        raise ValueError(
            "a projected reference is added with add_reference, not add_entity"
        )
    if entity.get("kind") not in ENTITY_KINDS:
        raise ValueError(
            f"entity kind {entity.get('kind')!r} is not one of {', '.join(ENTITY_KINDS)}"
        )
    entities = _list(feature, "entities")
    _ = entity.setdefault("id", next_id(entities, "e"))
    _put(feature, "entities", [*entities, entity])
    return {"entity_id": entity["id"]}


def _add_reference(
    feature: JSONObject, edit: JSONObject, project: Project | None = None
) -> JSONObject:
    """Projects an edge of an earlier sketch or body into this sketch, locked, as a construction entity."""
    source = _object(edit, "source")
    if project is None:
        raise ValueError(
            "projecting needs the document's earlier features to project from"
        )
    entities = _list(feature, "entities")
    for entity in entities:
        if entity.get("reference") == source:
            raise ValueError(f"that is already projected as {entity['id']!r}")
    try:
        fields = project(parse_reference(source))
    except Exception as exc:
        raise ValueError(f"cannot project that: {exc}") from None
    entity: JSONObject = {
        "id": next_id(entities, "e"),
        **projected_json(fields),
        "construction": True,
        "reference": source,
    }
    _put(feature, "entities", [*entities, entity])
    return {"entity_id": entity["id"]}


def _update_entity(
    feature: JSONObject, edit: JSONObject, _project: Project | None = None
) -> JSONObject:
    entities = _list(feature, "entities")
    entity = entities[_index_of(entities, edit.get("entity_id"), "entity")]
    if entity.get("reference"):
        raise ValueError(
            f"{entity['id']!r} is a projected reference and is locked: remove it, or change its source"
        )
    entity.update(_changes(edit))
    return {"entity_id": entity["id"]}


def _remove_entity(
    feature: JSONObject, edit: JSONObject, _project: Project | None = None
) -> JSONObject:
    """Removes the entity and every constraint that mentions it, as the viewer does."""
    entity_id = edit.get("entity_id")
    entities = _list(feature, "entities")
    del entities[_index_of(entities, entity_id, "entity")]
    _put(feature, "entities", entities)

    def mentions(constraint: JSONObject) -> bool:
        return any(
            isinstance(reference := constraint.get(key), str)
            and entity_id == reference.split(".")[0]
            for key in ("a", "b", "e")
        )

    kept = [
        constraint
        for constraint in _list(feature, "constraints")
        if not mentions(constraint)
    ]
    _put(feature, "constraints", kept)
    return {"entity_id": entity_id}


ACTIONS: dict[str, Action] = {
    "add_constraint": _add_constraint,
    "update_constraint": _update_constraint,
    "remove_constraint": _remove_constraint,
    "add_entity": _add_entity,
    "add_reference": _add_reference,
    "update_entity": _update_entity,
    "remove_entity": _remove_entity,
}


def edit_sketch(
    feature: JSONObject,
    edit: JSONObject,
    resolve: Callable[[JSONObject], JSONObject],
    project: Project | None = None,
) -> JSONObject:
    """Applies `edit` to the sketch `feature` in place, solves, and returns what changed.

    `resolve` turns a feature into one with its parameter expressions worked
    out. The solver needs numbers, but the stored constraints keep the
    expressions, so only the solved entities are copied back.

    `project` turns a reference's source into the entity fields it projects to
    (sketch_reference.py). A sketch with projected entities has them projected
    again before every solve, so the edit sees where their sources are now.

    A sketch that cannot be solved raises ValueError naming the constraints
    that conflict, and `feature` is left as it was.
    """
    action_name = edit.get("action")
    action = ACTIONS.get(action_name) if isinstance(action_name, str) else None
    if action is None:
        raise ValueError(
            f"unknown sketch action {action_name!r} (known: {', '.join(ACTIONS)})"
        )

    draft = copy.deepcopy(feature)
    assign_constraint_ids(draft)
    detail = action(draft, edit, project)
    stored = sketch_feature(draft)
    if stored is None:
        raise ValueError("only a sketch takes sketch edits")
    # The draft may still hold "=param" strings in its numbers. check_entities
    # reads only ids, kinds and which fields are present, never a number.
    check_entities(stored)

    constraints = json_objects(draft.get("constraints", []), "'constraints'")
    resolved_json = resolve(draft)
    resolved = sketch_feature(resolved_json)
    if resolved is None:
        raise ValueError("only a sketch takes sketch edits")
    if project is not None and has_references(resolved_json):
        resolved = with_references_updated(resolved, project)
    try:
        from sprocket_engine.solver import solve  # noqa: PLC0415 -- planegcs is an optional extra and slow to load
    except ImportError:
        raise ValueError(
            "editing a sketch needs the constraint solver: install sprocket-engine[solver]"
        ) from None
    result = solve(
        {
            "entities": resolved.get("entities", []),
            "constraints": resolved.get("constraints", []),
        }
    )
    if not result["ok"]:
        names = ", ".join(
            f"{constraints[index]['id']} ({constraints[index]['kind']})"
            for index in result["conflicting"]
        )
        raise ValueError(
            f"the sketch has no solution. These constraints conflict: {names}"
            if names
            else "the sketch has no solution"
        )

    draft["entities"] = entities_json(result["sketch"]["entities"])
    feature.clear()
    feature.update(draft)
    redundant: list[JSON] = [constraints[index]["id"] for index in result["redundant"]]
    return {
        **detail,
        "action": action_name,
        "dof": result["dof"],
        "redundant": redundant,
    }
