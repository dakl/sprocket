// What a sketch is and how it is edited: the pure half of the sketcher.
//
// A sketch is {entities, constraints}, the shape the document stores (see
// engine/src/sprocket_engine/ops/sketch.py). Every function here takes one and
// returns a new one, and none touches the DOM, three.js or the solver, so
// viewer/test/sketch-model.test.js runs them under node. sketcher.js turns
// pointer events into calls to these, then hands the result to the solver.
(function (root) {
  const TWO_PI = 2 * Math.PI;

  const solver = () => (typeof module !== "undefined" && module.exports ? require("./sketch-solver.js") : root.SketchSolver);
  const pointOf = (sketch, reference) => solver().pointOf(sketch, reference);
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const distance = ([ax, ay], [bx, by]) => Math.hypot(bx - ax, by - ay);

  function fail(message) {
    throw new Error(message);
  }

  function emptySketch() {
    return { entities: [], constraints: [] };
  }

  /// `e<n>`, one past the highest in use. Ids never change and never repeat
  /// within a sketch, because profiles and constraints refer to them: a
  /// deleted entity's id must not come back on a different shape.
  function nextId(sketch) {
    const used = sketch.entities.map((entity) => Number(entity.id.slice(1))).filter(Number.isFinite);
    return "e" + (Math.max(0, ...used) + 1);
  }

  /// `c<n>`, one past the highest in use. Same rule as entities: a constraint's
  /// id is its address for an agent, so a deleted one's id never comes back.
  function nextConstraintId(sketch) {
    const used = sketch.constraints.map((constraint) => Number((constraint.id || "").slice(1))).filter(Number.isFinite);
    return "c" + (Math.max(0, ...used) + 1);
  }

  /// A sketch whose constraints all have ids. Documents from before M3.8 have none.
  function withConstraintIds(sketch) {
    const next = copy(sketch);
    for (const constraint of next.constraints) if (!constraint.id) constraint.id = nextConstraintId(next);
    return next;
  }

  const ORIGIN = "origin";
  const isPointReference = (reference) => reference === ORIGIN || reference.includes(".");
  const entityIdOf = (reference) => reference.split(".")[0];

  function withEntity(sketch, entity) {
    const next = copy(sketch);
    next.entities.push(entity);
    return next;
  }

  function withConstraint(sketch, constraint) {
    const next = copy(sketch);
    const same = (other) =>
      other.kind === constraint.kind &&
      ["a", "b", "e"].every((key) => other[key] === constraint[key]) &&
      // Two dimensions on one thing are a conflict for the solver to report, not a duplicate to drop.
      other.value === constraint.value;
    if (!next.constraints.some(same)) next.constraints.push({ ...constraint, id: constraint.id || nextConstraintId(next) });
    return next;
  }

  function addLine(sketch, p0, p1) {
    const id = nextId(sketch);
    return { sketch: withEntity(sketch, { id, kind: "line", p0, p1 }), id };
  }

  function addCircle(sketch, centre, radius) {
    const id = nextId(sketch);
    return { sketch: withEntity(sketch, { id, kind: "circle", c: centre, r: radius }), id };
  }

  /// The circle through three points, or null when they are in a line.
  function circumcircle(a, b, c) {
    const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
    if (Math.abs(d) < 1e-9) return null;
    const a2 = a[0] ** 2 + a[1] ** 2;
    const b2 = b[0] ** 2 + b[1] ** 2;
    const c2 = c[0] ** 2 + c[1] ** 2;
    const centre = [
      (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
      (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d,
    ];
    return { c: centre, r: distance(centre, a) };
  }

  /// A counter-clockwise arc from `start` to `end` that passes through `through`.
  /// If going counter-clockwise from start reaches `through` before `end`, the
  /// arc starts at `start`. Otherwise the same three points describe the arc
  /// that runs from `end` back round to `start`.
  function arcThrough(start, end, through) {
    const circle = circumcircle(start, through, end);
    if (!circle) return null;
    const angleOf = (point) => Math.atan2(point[1] - circle.c[1], point[0] - circle.c[0]);
    const sweep = (from, to) => (((to - from) % TWO_PI) + TWO_PI) % TWO_PI;
    const aStart = angleOf(start);
    const aEnd = angleOf(end);
    const startsAtStart = sweep(aStart, angleOf(through)) < sweep(aStart, aEnd);
    const [a0, a1] = startsAtStart ? [aStart, aEnd] : [aEnd, aStart];
    return { c: circle.c, r: circle.r, a0, a1: a1 <= a0 ? a1 + TWO_PI : a1 };
  }

  function addArc(sketch, start, end, through) {
    const arc = arcThrough(start, end, through);
    if (!arc) return null;
    const id = nextId(sketch);
    return { sketch: withEntity(sketch, { id, kind: "arc", ...arc }), id };
  }

  /// Four lines joined at the corners and held horizontal and vertical, the
  /// constraints Fusion 360 gives a rectangle. Returns null for a flat one.
  function addRectangle(sketch, a, b) {
    if (Math.abs(a[0] - b[0]) < 1e-9 || Math.abs(a[1] - b[1]) < 1e-9) return null;
    const corners = [a, [b[0], a[1]], b, [a[0], b[1]]];
    let next = sketch;
    const ids = [];
    corners.forEach((corner, index) => {
      const added = addLine(next, corner, corners[(index + 1) % 4]);
      next = added.sketch;
      ids.push(added.id);
    });
    ids.forEach((id, index) => {
      next = withConstraint(next, { kind: "coincident", a: id + ".p1", b: ids[(index + 1) % 4] + ".p0" });
      next = withConstraint(next, { kind: index % 2 === 0 ? "horizontal" : "vertical", e: id });
    });
    return { sketch: next, ids };
  }

  function connect(sketch, first, second) {
    return first === second ? sketch : withConstraint(sketch, { kind: "coincident", a: first, b: second });
  }

  /// Joins the new point `fresh` to what a click snapped to: coincident with a
  /// point, on a curve, or nothing when it snapped to neither.
  function join(sketch, snapped, fresh) {
    if (snapped.reference) return connect(sketch, snapped.reference, fresh);
    if (snapped.curve) return withConstraint(sketch, { kind: "point_on", a: fresh, b: snapped.curve });
    return sketch;
  }

  /// Removes entities, and every constraint that mentions one.
  function deleteEntities(sketch, ids) {
    const gone = new Set(ids);
    const mentions = (constraint) => ["a", "b", "e"].some((key) => constraint[key] && gone.has(entityIdOf(constraint[key])));
    return {
      entities: sketch.entities.filter((entity) => !gone.has(entity.id)),
      constraints: sketch.constraints.filter((constraint) => !mentions(constraint)),
    };
  }

  const indexOfConstraint = (sketch, id) => sketch.constraints.findIndex((constraint) => constraint.id === id);

  /// The constraint at `index` replaced, keeping its id.
  function replaceConstraint(sketch, index, constraint) {
    const next = copy(sketch);
    next.constraints[index] = { ...constraint, id: constraint.id || next.constraints[index].id };
    return next;
  }

  function removeConstraint(sketch, index) {
    const next = copy(sketch);
    next.constraints.splice(index, 1);
    return next;
  }

  function removeConstraintById(sketch, id) {
    const index = indexOfConstraint(sketch, id);
    return index < 0 ? sketch : removeConstraint(sketch, index);
  }

  const referencesOf = (constraint) => ["a", "b", "e"].map((key) => constraint[key]).filter(Boolean);

  /// The entities a constraint acts on, for highlighting them. The origin and
  /// the sketch axes are not entities.
  function constraintEntities(sketch, constraint) {
    const known = new Set(sketch.entities.map((entity) => entity.id));
    return [...new Set(referencesOf(constraint).map(entityIdOf))].filter((id) => known.has(id));
  }

  /// "horizontal c3", or "distance 40.00 c9" for a dimension. Names a constraint in a message.
  function describeConstraint(constraint) {
    const value = typeof constraint.value === "number" ? " " + constraint.value.toFixed(2) : "";
    return constraint.kind + value + (constraint.id ? " " + constraint.id : "");
  }

  // --- Hit testing, in the sketch's own [u, v] ---------------------------

  function allPoints(sketch) {
    const own = sketch.entities.flatMap((entity) =>
      solver().POINTS[entity.kind].map((name) => ({ reference: entity.id + "." + name, uv: pointOf(sketch, entity.id + "." + name) }))
    );
    // Last, so a shape drawn from the origin snaps to its own point on a tie.
    return [...own, { reference: ORIGIN, uv: [0, 0] }];
  }

  /// The nearest point within `radius`, skipping `exclude`, or null.
  function snapTarget(sketch, uv, radius, exclude) {
    let best = null;
    for (const point of allPoints(sketch)) {
      if (exclude && exclude.includes(point.reference)) continue;
      const gap = distance(point.uv, uv);
      if (gap <= radius && (!best || gap < best.gap)) best = { reference: point.reference, uv: point.uv, gap };
    }
    return best;
  }

  const sweep = (from, to) => (((to - from) % TWO_PI) + TWO_PI) % TWO_PI;

  /// The point of `entity` closest to `uv`.
  function nearestOnEntity(entity, uv) {
    if (entity.kind === "line") {
      const [ax, ay] = entity.p0;
      const [bx, by] = entity.p1;
      const lengthSquared = (bx - ax) ** 2 + (by - ay) ** 2;
      const t = lengthSquared ? Math.max(0, Math.min(1, ((uv[0] - ax) * (bx - ax) + (uv[1] - ay) * (by - ay)) / lengthSquared)) : 0;
      return [ax + t * (bx - ax), ay + t * (by - ay)];
    }
    const fromCentre = distance(uv, entity.c);
    const angle = fromCentre ? Math.atan2(uv[1] - entity.c[1], uv[0] - entity.c[0]) : 0;
    const onCircle = [entity.c[0] + entity.r * Math.cos(angle), entity.c[1] + entity.r * Math.sin(angle)];
    if (entity.kind === "circle" || sweep(entity.a0, angle) <= sweep(entity.a0, entity.a1)) return onCircle;
    // Off the end of the arc: the nearer endpoint is the nearest part of it.
    const ends = ["p0", "p1"].map((name) => pointOf({ entities: [entity] }, entity.id + "." + name));
    return distance(uv, ends[0]) <= distance(uv, ends[1]) ? ends[0] : ends[1];
  }

  const distanceToEntity = (entity, uv) => distance(uv, nearestOnEntity(entity, uv));

  /// A point on a line, circle or arc within `radius` of `uv`: {entity, uv}.
  function snapToCurve(sketch, uv, radius) {
    let best = null;
    for (const entity of sketch.entities) {
      const onIt = nearestOnEntity(entity, uv);
      const gap = distance(onIt, uv);
      if (gap <= radius && (!best || gap < best.gap)) best = { entity: entity.id, kind: entity.kind, uv: onIt, gap };
    }
    return best;
  }

  /// A shown sketch axis within `radius` of `uv`. `axes` is a list of
  /// {ref, name}, `ref` being "axis_u" (the sketch's x) or "axis_v" (its y).
  function snapToAxis(axes, uv, radius) {
    let best = null;
    for (const axis of axes) {
      const onIt = axis.ref === "axis_u" ? [uv[0], 0] : [0, uv[1]];
      const gap = distance(onIt, uv);
      if (gap <= radius && (!best || gap < best.gap)) best = { ref: axis.ref, name: axis.name, uv: onIt, gap };
    }
    return best;
  }

  /// Where a click lands: a point if one is near, else the nearer of a curve
  /// and a shown axis, else the raw position. `reference` or `curve` says what
  /// it snapped to, and joining a new shape to it adds the matching constraint.
  function snap(sketch, uv, radius, axes = []) {
    const point = snapTarget(sketch, uv, radius);
    if (point) return { uv: point.uv, reference: point.reference, curve: null };
    const curve = snapToCurve(sketch, uv, radius);
    const axis = snapToAxis(axes, uv, radius);
    if (axis && (!curve || axis.gap < curve.gap)) return { uv: axis.uv, reference: null, curve: axis.ref, label: "On " + axis.name };
    if (curve) return { uv: curve.uv, reference: null, curve: curve.entity, label: "On " + curve.kind };
    return { uv, reference: null, curve: null };
  }

  /// What is under `uv`: a point if one is within `radius`, else the nearest
  /// entity within it, else null.
  function hit(sketch, uv, radius) {
    const point = snapTarget(sketch, uv, radius);
    if (point) return { kind: "point", reference: point.reference };
    let best = null;
    for (const entity of sketch.entities) {
      const gap = distanceToEntity(entity, uv);
      if (gap <= radius && (!best || gap < best.gap)) best = { kind: "entity", reference: entity.id, gap };
    }
    return best;
  }

  /// "horizontal" or "vertical" when `to` is within `toleranceDegrees` of that
  /// direction from `from`, else null. Drawing a line nearly level makes it level.
  function alignment(from, to, toleranceDegrees) {
    const dx = Math.abs(to[0] - from[0]);
    const dy = Math.abs(to[1] - from[1]);
    if (dx + dy < 1e-9) return null;
    const tolerance = Math.tan((toleranceDegrees * Math.PI) / 180);
    if (dy <= dx * tolerance) return "horizontal";
    if (dx <= dy * tolerance) return "vertical";
    return null;
  }

  // --- Constraints and dimensions from a selection ------------------------

  const kindOf = (sketch, reference) => sketch.entities.find((entity) => entity.id === entityIdOf(reference)).kind;
  const isLine = (sketch, reference) => !isPointReference(reference) && kindOf(sketch, reference) === "line";
  const isCurve = (sketch, reference) => !isPointReference(reference) && kindOf(sketch, reference) !== "line";

  /// The constraint `kind` applied to the selected references, or an Error
  /// that says what to select instead.
  function constraintFor(sketch, kind, selection) {
    const [first, second] = selection;
    switch (kind) {
      case "coincident":
        if (selection.length === 2 && selection.every(isPointReference)) return { kind, a: first, b: second };
        return fail("Select two points to make them coincident");
      case "horizontal":
      case "vertical":
        if (selection.length === 1 && isLine(sketch, first)) return { kind, e: first };
        if (selection.length === 2 && selection.every(isPointReference)) return { kind, a: first, b: second };
        return fail("Select a line, or two points, to make " + (kind === "horizontal" ? "level" : "upright"));
      case "parallel":
      case "perpendicular":
        if (selection.length === 2 && selection.every((reference) => isLine(sketch, reference))) return { kind, a: first, b: second };
        return fail("Select two lines to make them " + kind);
      case "equal":
        if (selection.length === 2 && selection.every((reference) => !isPointReference(reference))) {
          const kinds = selection.map((reference) => kindOf(sketch, reference));
          if (kinds[0] === "line" ? kinds[1] === "line" : kinds[1] !== "line") return { kind, a: first, b: second };
        }
        return fail("Select two lines, or two circles or arcs, to make them equal");
      case "tangent":
        if (selection.length === 2 && selection.every((reference) => !isPointReference(reference)) && selection.some((reference) => isCurve(sketch, reference))) {
          return { kind, a: first, b: second };
        }
        return fail("Select a circle or arc and a line, circle or arc to make them tangent");
      case "point_on": {
        const [point, curve] = selection.length === 2 && isPointReference(first) ? [first, second] : [second, first];
        if (selection.length === 2 && isPointReference(point) && !isPointReference(curve) && curve !== entityIdOf(point)) return { kind, a: point, b: curve };
        return fail("Select a point and a line, circle or arc to put it on");
      }
      case "fix":
        if (selection.length === 1 && first === ORIGIN) return fail("The origin is already fixed");
        if (selection.length === 1) return { kind, e: first };
        return fail("Select one point or shape to fix");
      default:
        return fail("unknown constraint '" + kind + "'");
    }
  }

  /// The dimension a selection can carry, with its current value so that
  /// accepting it unchanged moves nothing, and where to put its label. Null
  /// when the selection has none.
  function suggestDimension(sketch, selection) {
    const midpoint = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    if (selection.length === 1 && !isPointReference(selection[0])) {
      const entity = sketch.entities.find((item) => item.id === selection[0]);
      if (entity.kind === "line") {
        return { constraint: { kind: "distance", a: entity.id + ".p0", b: entity.id + ".p1", value: distance(entity.p0, entity.p1) }, anchor: midpoint(entity.p0, entity.p1) };
      }
      if (entity.kind === "circle") return { constraint: { kind: "diameter", e: entity.id, value: entity.r * 2 }, anchor: entity.c };
      return { constraint: { kind: "radius", e: entity.id, value: entity.r }, anchor: entity.c };
    }
    if (selection.length === 2 && selection.every(isPointReference)) {
      const [a, b] = selection.map((reference) => pointOf(sketch, reference));
      return { constraint: { kind: "distance", a: selection[0], b: selection[1], value: distance(a, b) }, anchor: midpoint(a, b) };
    }
    if (selection.length === 2 && selection.every((reference) => isLine(sketch, reference))) {
      const [one, two] = selection.map((id) => sketch.entities.find((entity) => entity.id === id));
      const d1 = [one.p1[0] - one.p0[0], one.p1[1] - one.p0[1]];
      const d2 = [two.p1[0] - two.p0[0], two.p1[1] - two.p0[1]];
      // planegcs measures the angle counter-clockwise from the first line to the second.
      const degrees = (Math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1]) * 180) / Math.PI;
      return { constraint: { kind: "angle", a: one.id, b: two.id, value: degrees }, anchor: midpoint(one.p1, two.p1) };
    }
    return null;
  }

  /// Where a dimension constraint's label sits, in [u, v]. Null for constraints without one.
  function dimensionAnchor(sketch, constraint) {
    const suggestion = suggestDimension(
      sketch,
      constraint.kind === "distance"
        ? [constraint.a, constraint.b].map((reference) => reference)
        : constraint.kind === "angle"
          ? [constraint.a, constraint.b]
          : constraint.e
            ? [constraint.e]
            : []
    );
    if (!suggestion) return null;
    // A distance between a line's own two ends is drawn on the line.
    return suggestion.anchor;
  }

  /// A point on `reference` to hang a glyph on: a point is itself, a line is its
  /// middle, a circle or arc is a spot on its curve.
  function referenceAnchor(sketch, reference) {
    if (reference === ORIGIN || !reference.includes(".") && !sketch.entities.some((entity) => entity.id === reference)) return [0, 0];
    if (isPointReference(reference)) return pointOf(sketch, reference);
    const entity = sketch.entities.find((item) => item.id === reference);
    if (entity.kind === "line") return [(entity.p0[0] + entity.p1[0]) / 2, (entity.p0[1] + entity.p1[1]) / 2];
    const angle = entity.kind === "arc" ? (entity.a0 + (entity.a1 > entity.a0 ? entity.a1 : entity.a1 + TWO_PI)) / 2 : Math.PI / 4;
    return [entity.c[0] + entity.r * Math.cos(angle), entity.c[1] + entity.r * Math.sin(angle)];
  }

  /// Where the glyphs of a constraint that has no value sit, in [u, v]: one
  /// beside each shape it ties together, or one for a constraint on a single
  /// thing. Dimensions have a label instead (see `dimensionAnchor`).
  function glyphAnchors(sketch, constraint) {
    const anchor = (reference) => referenceAnchor(sketch, reference);
    switch (constraint.kind) {
      case "coincident":
      case "point_on":
        return [anchor(constraint.a)];
      case "horizontal":
      case "vertical":
        if (constraint.e) return [anchor(constraint.e)];
        return [[(anchor(constraint.a)[0] + anchor(constraint.b)[0]) / 2, (anchor(constraint.a)[1] + anchor(constraint.b)[1]) / 2]];
      case "fix":
        return [anchor(constraint.e)];
      default:
        return referencesOf(constraint).map(anchor);
    }
  }

  const sameSketch = (a, b) => JSON.stringify([a.entities, a.constraints]) === JSON.stringify([b.entities, b.constraints]);

  /// A feature the document accepts: `plane` as the viewer holds it (a name or
  /// {origin, normal, x_dir}) plus the sketch.
  function toFeature(plane, sketch) {
    return { plane, entities: copy(sketch.entities), constraints: copy(sketch.constraints) };
  }

  const api = {
    emptySketch,
    nextId,
    addLine,
    addCircle,
    addArc,
    addRectangle,
    arcThrough,
    circumcircle,
    connect,
    join,
    snap,
    nextConstraintId,
    withConstraintIds,
    withConstraint,
    replaceConstraint,
    removeConstraint,
    removeConstraintById,
    indexOfConstraint,
    constraintEntities,
    describeConstraint,
    glyphAnchors,
    sameSketch,
    deleteEntities,
    snapTarget,
    hit,
    alignment,
    constraintFor,
    suggestDimension,
    dimensionAnchor,
    toFeature,
    isPointReference,
    entityIdOf,
    ORIGIN,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SketchModel = api;
})(typeof window !== "undefined" ? window : globalThis);
