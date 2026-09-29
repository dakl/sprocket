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

  const isPointReference = (reference) => reference.includes(".");
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
    if (!next.constraints.some(same)) next.constraints.push(constraint);
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

  /// Removes entities, and every constraint that mentions one.
  function deleteEntities(sketch, ids) {
    const gone = new Set(ids);
    const mentions = (constraint) => ["a", "b", "e"].some((key) => constraint[key] && gone.has(entityIdOf(constraint[key])));
    return {
      entities: sketch.entities.filter((entity) => !gone.has(entity.id)),
      constraints: sketch.constraints.filter((constraint) => !mentions(constraint)),
    };
  }

  function replaceConstraint(sketch, index, constraint) {
    const next = copy(sketch);
    next.constraints[index] = constraint;
    return next;
  }

  function removeConstraint(sketch, index) {
    const next = copy(sketch);
    next.constraints.splice(index, 1);
    return next;
  }

  // --- Hit testing, in the sketch's own [u, v] ---------------------------

  function allPoints(sketch) {
    return sketch.entities.flatMap((entity) =>
      solver().POINTS[entity.kind].map((name) => ({ reference: entity.id + "." + name, uv: pointOf(sketch, entity.id + "." + name) }))
    );
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

  function distanceToEntity(entity, uv) {
    if (entity.kind === "line") {
      const [ax, ay] = entity.p0;
      const [bx, by] = entity.p1;
      const lengthSquared = (bx - ax) ** 2 + (by - ay) ** 2;
      const t = lengthSquared ? Math.max(0, Math.min(1, ((uv[0] - ax) * (bx - ax) + (uv[1] - ay) * (by - ay)) / lengthSquared)) : 0;
      return distance(uv, [ax + t * (bx - ax), ay + t * (by - ay)]);
    }
    const fromCentre = distance(uv, entity.c);
    if (entity.kind === "circle") return Math.abs(fromCentre - entity.r);
    const sweep = (from, to) => (((to - from) % TWO_PI) + TWO_PI) % TWO_PI;
    const angle = Math.atan2(uv[1] - entity.c[1], uv[0] - entity.c[0]);
    if (sweep(entity.a0, angle) <= sweep(entity.a0, entity.a1)) return Math.abs(fromCentre - entity.r);
    // Off the end of the arc: the nearer endpoint is the nearest part of it.
    return Math.min(distance(uv, pointOf({ entities: [entity] }, entity.id + ".p0")), distance(uv, pointOf({ entities: [entity] }, entity.id + ".p1")));
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
      case "fix":
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
    withConstraint,
    replaceConstraint,
    removeConstraint,
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
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SketchModel = api;
})(typeof window !== "undefined" ? window : globalThis);
