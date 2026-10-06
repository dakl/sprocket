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

from .ops.sketch import CONSTRAINT_FIELDS, check_entities
from .sketch_reference import has_references, with_references_updated

ENTITY_KINDS = ("line", "arc", "circle")
# What an update may not change: the id is the address, and a different kind
# is a different shape, which is a remove and an add.
FIXED_KEYS = ("id", "kind", "reference")


def next_id(items: list[dict], prefix: str) -> str:
    """`<prefix><n>`, one past the highest in use, so a deleted id does not come back on another shape."""
    used = [int(item["id"][len(prefix):]) for item in items if item.get("id", "").startswith(prefix) and item["id"][len(prefix):].isdigit()]
    return f"{prefix}{max(used, default=0) + 1}"


def assign_constraint_ids(feature: dict) -> None:
    """Gives every constraint without an id one. Documents from before M3.8 have none."""
    constraints = feature.get("constraints", [])
    for constraint in constraints:
        if "id" not in constraint:
            constraint["id"] = next_id(constraints, "c")


def _index_of(items: list[dict], item_id, what: str) -> int:
    for index, item in enumerate(items):
        if item["id"] == item_id:
            return index
    raise ValueError(f"no {what} {item_id!r}")


def _changes(edit: dict) -> dict:
    changes = edit.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("'changes' must be an object with at least one field")
    forbidden = [key for key in FIXED_KEYS if key in changes]
    if forbidden:
        raise ValueError(f"'changes' cannot set {', '.join(forbidden)}")
    return changes


def _object(edit: dict, key: str) -> dict:
    value = edit.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"'{key}' must be an object")
    return dict(value)


def _add_constraint(feature: dict, edit: dict, project=None) -> dict:
    constraint = _object(edit, "constraint")
    if constraint.get("kind") not in CONSTRAINT_FIELDS:
        raise ValueError(f"constraint kind {constraint.get('kind')!r} is not one of {', '.join(sorted(CONSTRAINT_FIELDS))}")
    constraints = feature.setdefault("constraints", [])
    constraint.setdefault("id", next_id(constraints, "c"))
    constraints.append(constraint)
    return {"constraint_id": constraint["id"]}


def _update_constraint(feature: dict, edit: dict, project=None) -> dict:
    constraints = feature.get("constraints", [])
    constraint = constraints[_index_of(constraints, edit.get("constraint_id"), "constraint")]
    constraint.update(_changes(edit))
    return {"constraint_id": constraint["id"]}


def _remove_constraint(feature: dict, edit: dict, project=None) -> dict:
    constraints = feature.get("constraints", [])
    del constraints[_index_of(constraints, edit.get("constraint_id"), "constraint")]
    return {"constraint_id": edit["constraint_id"]}


def _add_entity(feature: dict, edit: dict, project=None) -> dict:
    entity = _object(edit, "entity")
    if "reference" in entity:
        raise ValueError("a projected reference is added with add_reference, not add_entity")
    if entity.get("kind") not in ENTITY_KINDS:
        raise ValueError(f"entity kind {entity.get('kind')!r} is not one of {', '.join(ENTITY_KINDS)}")
    entity.setdefault("id", next_id(feature["entities"], "e"))
    feature["entities"].append(entity)
    return {"entity_id": entity["id"]}


def _add_reference(feature: dict, edit: dict, project=None) -> dict:
    """Projects an edge of an earlier sketch or body into this sketch, locked, as a construction entity."""
    source = _object(edit, "source")
    if project is None:
        raise ValueError("projecting needs the document's earlier features to project from")
    for entity in feature["entities"]:
        if entity.get("reference") == source:
            raise ValueError(f"that is already projected as {entity['id']!r}")
    try:
        fields = project(source)
    except Exception as exc:
        raise ValueError(f"cannot project that: {exc}") from None
    entity = {"id": next_id(feature["entities"], "e"), **fields, "construction": True, "reference": source}
    feature["entities"].append(entity)
    return {"entity_id": entity["id"]}


def _update_entity(feature: dict, edit: dict, project=None) -> dict:
    entity = feature["entities"][_index_of(feature["entities"], edit.get("entity_id"), "entity")]
    if entity.get("reference"):
        raise ValueError(f"{entity['id']!r} is a projected reference and is locked: remove it, or change its source")
    entity.update(_changes(edit))
    return {"entity_id": entity["id"]}


def _remove_entity(feature: dict, edit: dict, project=None) -> dict:
    """Removes the entity and every constraint that mentions it, as the viewer does."""
    entity_id = edit.get("entity_id")
    del feature["entities"][_index_of(feature["entities"], entity_id, "entity")]

    def mentions(constraint: dict) -> bool:
        return any(entity_id == constraint[key].split(".")[0] for key in ("a", "b", "e") if key in constraint)

    feature["constraints"] = [constraint for constraint in feature.get("constraints", []) if not mentions(constraint)]
    return {"entity_id": entity_id}


ACTIONS = {
    "add_constraint": _add_constraint,
    "update_constraint": _update_constraint,
    "remove_constraint": _remove_constraint,
    "add_entity": _add_entity,
    "add_reference": _add_reference,
    "update_entity": _update_entity,
    "remove_entity": _remove_entity,
}


def _solve(sketch: dict) -> dict:
    try:
        from . import solver
    except ImportError:
        raise ValueError("editing a sketch needs the constraint solver: install sprocket-engine[solver]") from None
    return solver.solve(sketch)


def edit_sketch(feature: dict, edit: dict, resolve, project=None) -> dict:
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
    action = ACTIONS.get(edit.get("action"))
    if action is None:
        raise ValueError(f"unknown sketch action {edit.get('action')!r} (known: {', '.join(ACTIONS)})")

    draft = copy.deepcopy(feature)
    assign_constraint_ids(draft)
    detail = action(draft, edit, project)
    check_entities(draft)

    constraints = draft.get("constraints", [])
    resolved = resolve(draft)
    if project is not None and has_references(resolved):
        resolved = with_references_updated(resolved, project)
    result = _solve({"entities": resolved["entities"], "constraints": resolved.get("constraints", [])})
    if not result["ok"]:
        names = ", ".join(f"{constraints[index]['id']} ({constraints[index]['kind']})" for index in result["conflicting"])
        raise ValueError(f"the sketch has no solution. These constraints conflict: {names}" if names else "the sketch has no solution")

    draft["entities"] = result["sketch"]["entities"]
    feature.clear()
    feature.update(draft)
    return {
        **detail,
        "action": edit["action"],
        "dof": result["dof"],
        "redundant": [constraints[index]["id"] for index in result["redundant"]],
    }
