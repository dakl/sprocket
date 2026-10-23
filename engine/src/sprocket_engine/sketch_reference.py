"""Projected reference geometry: an edge of an earlier sketch or body, seen
from inside another sketch.

A sketch's constraints only join its own entities. To dimension one sketch
against another, the other sketch's edge is projected onto this sketch's plane
and stored as an entity that carries `reference`, the name of its source:

    {"sketch": "f1", "entity": "e3"}              an entity of an earlier sketch
    {"feature": "f2", "edge": <selector>}         one edge of an earlier feature's body

The solver treats such an entity as locked (solver.py), so constraints can name
it and nothing can move it. Its numbers are only a copy: `refresh_references`
projects the source again on every rebuild and solves the sketch, which is how a
dimension to it survives a change in the sketch it came from.
"""

import copy
import math
from collections.abc import Callable
from typing import Required, TypedDict, final

from build123d import Edge, GeomType, Plane, Vector

from sprocket_engine.b3d import to_local
from sprocket_engine.context import Context
from sprocket_engine.model import (
    Entity,
    EntityKind,
    EntityReference,
    JSONObject,
    PlaneRef,
    SketchFeature,
    sketch_feature,
    to_json,
)
from sprocket_engine.ops.sketch import resolve_plane, sketch_entities
from sprocket_engine.regions import entity_edge
from sprocket_engine.selectors import resolve_selector

TWO_PI = 2 * math.pi
# How parallel an edge's plane must be to the sketch plane for a circle to stay a circle.
FLAT = 1 - 1e-6


@final
class Projected(TypedDict, total=False):
    """An entity's kind and geometry, as a projection gives them: no id."""

    kind: Required[EntityKind]
    p0: list[float]
    p1: list[float]
    c: list[float]
    r: float
    a0: float
    a1: float


type Project = Callable[[EntityReference], Projected]


def has_references(feature: JSONObject) -> bool:
    """Whether any entity of the sketch is a projected reference."""
    entities = feature.get("entities")
    if not isinstance(entities, list):
        return False
    return any(
        isinstance(entity, dict) and bool(entity.get("reference"))
        for entity in entities
    )


def source_edge(source: EntityReference, ctx: Context) -> Edge:
    """The edge `source` names, in world coordinates, found in what replay has built so far."""
    if "sketch" in source:
        sketch = ctx.sketch(source["sketch"])
        if sketch is None or "entities" not in sketch:
            raise ValueError(
                f"{source['sketch']!r} is not a sketch of entities built before this one"
            )
        found = [
            entity
            for entity in sketch_entities(sketch)
            if entity["id"] == source.get("entity")
        ]
        if not found:
            raise ValueError(
                f"sketch {source['sketch']!r} has no entity {source.get('entity')!r}"
            )
        return resolve_plane(sketch["plane"], ctx) * entity_edge(found[0])
    if "feature" in source and "edge" in source:
        shape = ctx.results.get(source["feature"])
        if shape is None:
            raise ValueError(f"{source['feature']!r} is not built before this sketch")
        edges = resolve_selector(shape, source["edge"], ctx)
        if len(edges) != 1:
            raise ValueError(
                f"the edge selector matched {len(edges)} edges of {source['feature']!r}, expected exactly 1"
            )
        edge = edges[0]
        if not isinstance(edge, Edge):
            raise ValueError("a reference's edge selector must match an edge")
        return edge
    raise ValueError("a reference names {'sketch', 'entity'} or {'feature', 'edge'}")


def _sweep(start: float, end: float) -> float:
    return (end - start) % TWO_PI


def project_edge(edge: Edge, plane: Plane) -> Projected:
    """The entity fields (kind and geometry) of `edge` seen in `plane`'s own [u, v].

    A straight edge projects whatever its angle to the plane. A circle only
    stays a circle when it lies parallel to the plane, and a slanted one would
    be an ellipse, which a sketch cannot hold.
    """

    def local(point: Vector) -> list[float]:
        found = to_local(plane, point)
        return [round(found.X, 9) + 0.0, round(found.Y, 9) + 0.0]

    if edge.geom_type == GeomType.LINE:
        p0, p1 = local(edge.start_point()), local(edge.end_point())
        if math.dist(p0, p1) < 1e-9:
            raise ValueError(
                "that edge runs straight at the sketch plane, so it projects to a point"
            )
        return {"kind": "line", "p0": p0, "p1": p1}
    if edge.geom_type != GeomType.CIRCLE:
        raise ValueError(
            f"only straight and circular edges can be projected, not a {edge.geom_type.name.lower()} one"
        )
    if abs(edge.normal().dot(plane.z_dir)) < FLAT:
        raise ValueError(
            "a circle projects as a circle only onto a plane parallel to it"
        )
    centre = local(edge.arc_center)
    radius = round(edge.radius, 9)
    if edge.is_closed:
        return {"kind": "circle", "c": centre, "r": radius}
    start, middle, end = (local(edge.position_at(t)) for t in (0.0, 0.5, 1.0))

    def angle(point: list[float]) -> float:
        return math.atan2(point[1] - centre[1], point[0] - centre[0])

    a0, a1 = angle(start), angle(end)
    # Looking from the sketch's side the arc may run the other way round.
    if _sweep(a0, angle(middle)) > _sweep(a0, a1):
        a0, a1 = a1, a0
    while a1 <= a0:
        a1 += TWO_PI
    return {"kind": "arc", "c": centre, "r": radius, "a0": a0, "a1": a1}


def projector(plane_reference: PlaneRef, ctx: Context) -> Project:
    """A function from a source to entity fields, for a sketch on `plane_reference`."""
    plane = resolve_plane(plane_reference, ctx)
    return lambda source: project_edge(source_edge(source, ctx), plane)


def with_references_updated(feature: SketchFeature, project: Project) -> SketchFeature:
    """A copy of `feature` whose reference entities are projected again. Nothing is solved."""
    updated = copy.deepcopy(feature)
    for entity in updated.get("entities", []):
        source = entity.get("reference")
        if not source:
            continue
        try:
            fields = project(source)
        except Exception as exc:
            raise ValueError(
                f"reference {entity['id']!r} of sketch {feature['id']!r}: {exc}"
            ) from None
        if fields["kind"] != entity["kind"]:
            raise ValueError(
                f"reference {entity['id']!r} of sketch {feature['id']!r} was a {entity['kind']} and its source is now a {fields['kind']}"
            )
        _take(entity, fields)
    return updated


def _take(entity: Entity, fields: Projected) -> None:
    """Copy a projection's kind and geometry onto `entity`."""
    entity["kind"] = fields["kind"]
    if "p0" in fields:
        entity["p0"] = fields["p0"]
    if "p1" in fields:
        entity["p1"] = fields["p1"]
    if "c" in fields:
        entity["c"] = fields["c"]
    if "r" in fields:
        entity["r"] = fields["r"]
    if "a0" in fields:
        entity["a0"] = fields["a0"]
    if "a1" in fields:
        entity["a1"] = fields["a1"]


def refresh_references(feature: JSONObject, ctx: Context) -> JSONObject:
    """`feature` with its projected entities brought up to date and the sketch solved around them.

    `feature` is resolved, numbers only. Without the solver the entities are
    still refreshed, but nothing else moves until a viewer or an agent edits the sketch.
    """
    sketch = sketch_feature(feature)
    if sketch is None or not has_references(feature):
        return feature
    updated = with_references_updated(sketch, projector(sketch["plane"], ctx))
    try:
        from sprocket_engine import solver
    except ImportError:
        return to_json(updated)
    result = solver.solve(
        {
            "entities": updated.get("entities", []),
            "constraints": updated.get("constraints", []),
        }
    )
    if not result["ok"]:
        constraints = updated.get("constraints", [])
        names = ", ".join(
            f"{constraints[index].get('id', index)} ({constraints[index]['kind']})"
            for index in result["conflicting"]
        )
        raise ValueError(
            f"sketch {feature['id']!r} has no solution now that its references moved"
            + (f": {names} conflict" if names else "")
        )
    solved: SketchFeature = {**updated, "entities": result["sketch"]["entities"]}
    return to_json(solved)
