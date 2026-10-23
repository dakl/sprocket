// What a sketch is and how it is edited: the pure half of the sketcher.
//
// A sketch is {entities, constraints}, the shape the document stores (see
// engine/src/sprocket_engine/ops/sketch.py). Every function here takes one and
// returns a new one, and none touches the DOM, three.js or the solver, so
// viewer/test/sketch-model.test.js runs them under node. sketcher.js turns
// pointer events into calls to these, then hands the result to the solver.
((root) => {
  const TWO_PI = 2 * Math.PI;

  const solver = () =>
    typeof module !== "undefined" && module.exports
      ? require("./sketch-solver.js")
      : root.SketchSolver;
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
    const used = sketch.entities
      .map((entity) => Number(entity.id.slice(1)))
      .filter(Number.isFinite);
    return `e${Math.max(0, ...used) + 1}`;
  }

  /// `c<n>`, one past the highest in use. Same rule as entities: a constraint's
  /// id is its address for an agent, so a deleted one's id never comes back.
  function nextConstraintId(sketch) {
    const used = sketch.constraints
      .map((constraint) => Number((constraint.id || "").slice(1)))
      .filter(Number.isFinite);
    return `c${Math.max(0, ...used) + 1}`;
  }

  /// A sketch whose constraints all have ids. Documents from before M3.8 have none.
  function withConstraintIds(sketch) {
    const next = copy(sketch);
    for (const constraint of next.constraints) {
      if (!constraint.id) {
        constraint.id = nextConstraintId(next);
      }
    }
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
    if (!next.constraints.some(same)) {
      next.constraints.push({ ...constraint, id: constraint.id || nextConstraintId(next) });
    }
    return next;
  }

  /// An edge of another sketch or a body, projected into this one and locked.
  /// `fields` is the entity's kind and geometry as the engine projected it, and
  /// `source` names what it came from. Null when that edge is already here.
  function addReference(sketch, source, fields) {
    if (
      sketch.entities.some(
        (entity) => entity.reference && JSON.stringify(entity.reference) === JSON.stringify(source),
      )
    ) {
      return null;
    }
    const id = nextId(sketch);
    return {
      sketch: withEntity(sketch, {
        id,
        ...copy(fields),
        construction: true,
        reference: copy(source),
      }),
      id,
    };
  }

  /// Copies of the entities `ids`, mirrored across the line `lineId`, each tied
  /// to its original by `symmetric` constraints so the copy follows when the
  /// original moves. A circle's copy also has an equal radius, and an arc's follows from its three points. An arc runs the
  /// other way round once mirrored, so its copy's start is the mirror of the
  /// original's end. The mirror line itself is not copied. Returns
  /// {sketch, ids} with the new entity ids, or throws an Error.
  function mirrorEntities(sketch, ids, lineId) {
    const axis = sketch.entities.find((entity) => entity.id === lineId);
    if (axis?.kind !== "line") {
      fail("Mirror across a line");
    }
    const [ax, ay] = axis.p0;
    const [dx, dy] = [axis.p1[0] - ax, axis.p1[1] - ay];
    const length = Math.hypot(dx, dy);
    if (length < 1e-9) {
      fail("The mirror line has no length");
    }
    const [ux, uy] = [dx / length, dy / length];
    const reflect = ([x, y]) => {
      const along = (x - ax) * ux + (y - ay) * uy;
      const foot = [ax + along * ux, ay + along * uy];
      return [2 * foot[0] - x, 2 * foot[1] - y];
    };
    const mirrorAngle = 2 * Math.atan2(uy, ux);
    const sources = sketch.entities.filter(
      (entity) => ids.includes(entity.id) && entity.id !== lineId,
    );
    if (sources.length === 0) {
      fail("Select the shapes to mirror, then the line to mirror across");
    }
    const next = copy(sketch);
    const created = [];
    for (const source of sources) {
      const id = nextId(next);
      created.push(id);
      const entity = { id, kind: source.kind };
      if (source.construction) {
        entity.construction = true;
      }
      const tie = (from, to) =>
        next.constraints.push({
          kind: "symmetric",
          a: from,
          b: to,
          e: lineId,
          id: nextConstraintId(next),
        });
      if (source.kind === "line") {
        entity.p0 = reflect(source.p0);
        entity.p1 = reflect(source.p1);
        next.entities.push(entity);
        tie(`${source.id}.p0`, `${id}.p0`);
        tie(`${source.id}.p1`, `${id}.p1`);
      } else {
        entity.c = reflect(source.c);
        entity.r = source.r;
        if (source.kind === "arc") {
          entity.a0 = mirrorAngle - source.a1;
          entity.a1 = mirrorAngle - source.a0;
          while (entity.a1 <= entity.a0) {
            entity.a1 += TWO_PI;
          }
        }
        next.entities.push(entity);
        tie(`${source.id}.c`, `${id}.c`);
        if (source.kind === "arc") {
          tie(`${source.id}.p0`, `${id}.p1`);
          tie(`${source.id}.p1`, `${id}.p0`);
        }
        if (source.kind === "circle") {
          next.constraints.push({ kind: "equal", a: source.id, b: id, id: nextConstraintId(next) });
        }
      }
    }
    return { sketch: next, ids: created };
  }

  /// The sketch with the construction flag flipped on the entities `ids` name:
  /// all of them become construction, or if they all were, none do. A projected
  /// reference is always construction and is skipped. Null when nothing is left to flip.
  function toggleConstruction(sketch, ids) {
    const targets = new Set(
      sketch.entities
        .filter((entity) => ids.includes(entity.id) && !entity.reference)
        .map((entity) => entity.id),
    );
    if (targets.size === 0) {
      return null;
    }
    const make = !sketch.entities
      .filter((entity) => targets.has(entity.id))
      .every((entity) => entity.construction);
    const next = copy(sketch);
    for (const entity of next.entities) {
      if (!targets.has(entity.id)) {
        continue;
      }
      if (make) {
        entity.construction = true;
      } else {
        delete entity.construction;
      }
    }
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
    if (Math.abs(d) < 1e-9) {
      return null;
    }
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
    if (!circle) {
      return null;
    }
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
    if (!arc) {
      return null;
    }
    const id = nextId(sketch);
    return { sketch: withEntity(sketch, { id, kind: "arc", ...arc }), id };
  }

  /// Four lines joined at the corners and held horizontal and vertical, the
  /// constraints Fusion 360 gives a rectangle. Returns null for a flat one.
  function addRectangle(sketch, a, b) {
    if (Math.abs(a[0] - b[0]) < 1e-9 || Math.abs(a[1] - b[1]) < 1e-9) {
      return null;
    }
    const corners = [a, [b[0], a[1]], b, [a[0], b[1]]];
    let next = sketch;
    const ids = [];
    corners.forEach((corner, index) => {
      const added = addLine(next, corner, corners[(index + 1) % 4]);
      next = added.sketch;
      ids.push(added.id);
    });
    ids.forEach((id, index) => {
      next = withConstraint(next, {
        kind: "coincident",
        a: `${id}.p1`,
        b: `${ids[(index + 1) % 4]}.p0`,
      });
      next = withConstraint(next, { kind: index % 2 === 0 ? "horizontal" : "vertical", e: id });
    });
    return { sketch: next, ids };
  }

  function connect(sketch, first, second) {
    return first === second
      ? sketch
      : withConstraint(sketch, { kind: "coincident", a: first, b: second });
  }

  /// Joins the new point `fresh` to what a click snapped to: coincident with a
  /// point, on a curve, or nothing when it snapped to neither.
  function join(sketch, snapped, fresh) {
    if (snapped.reference) {
      return connect(sketch, snapped.reference, fresh);
    }
    if (snapped.curve) {
      return withConstraint(sketch, { kind: "point_on", a: fresh, b: snapped.curve });
    }
    return sketch;
  }

  /// The lines that end at the point `reference`, counting every point joined to
  /// it by coincident constraints: [{id, end}] with `end` "p0" or "p1". A corner
  /// of two lines gives two.
  function lineEndsAt(sketch, reference) {
    const joined = new Set([reference]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const constraint of sketch.constraints) {
        if (constraint.kind !== "coincident") {
          continue;
        }
        const [a, b] = [constraint.a, constraint.b];
        if (joined.has(a) !== joined.has(b)) {
          joined.add(a);
          joined.add(b);
          grew = true;
        }
      }
    }
    return [...joined]
      .filter((point) => point !== ORIGIN)
      .map((point) => ({ id: entityIdOf(point), end: point.split(".")[1], reference: point }))
      .filter(
        ({ id, end }) =>
          (end === "p0" || end === "p1") &&
          sketch.entities.some((entity) => entity.id === id && entity.kind === "line"),
      );
  }

  /// Rounds the corner of two lines at `reference` with an arc of `radius`: each
  /// line is cut back to where the arc touches it, and the arc is tangent to
  /// both with that radius. Every coincident constraint on the two line ends is replaced by
  /// the arc's own. Returns {sketch, id} for the arc, or throws an Error that
  /// says why the corner cannot be rounded.
  function addFillet(sketch, reference, radius) {
    const ends = lineEndsAt(sketch, reference);
    if (ends.length !== 2 || ends[0].id === ends[1].id) {
      fail("Click a point where exactly two lines meet");
    }
    if (!(radius > 0)) {
      fail("The radius must be above zero");
    }
    const corner = pointOf(sketch, reference);
    const lines = ends.map((end) => sketch.entities.find((entity) => entity.id === end.id));
    const far = ends.map((end, index) => lines[index][end.end === "p0" ? "p1" : "p0"]);
    const lengths = far.map((point) => distance(point, corner));
    if (lengths.some((length) => length < 1e-9)) {
      fail("A line there has no length");
    }
    const [d1, d2] = far.map((point, index) => [
      (point[0] - corner[0]) / lengths[index],
      (point[1] - corner[1]) / lengths[index],
    ]);
    const cosine = Math.max(-1, Math.min(1, d1[0] * d2[0] + d1[1] * d2[1]));
    const angle = Math.acos(cosine);
    if (angle < 1e-3 || angle > Math.PI - 1e-3) {
      fail("Those two lines run along each other, so there is no corner to round");
    }
    const reach = radius / Math.tan(angle / 2);
    if (reach > 0.999 * Math.min(...lengths)) {
      fail("The radius is too big for these lines: the arc would start past the end of one");
    }
    const touch = [d1, d2].map((direction) => [
      corner[0] + direction[0] * reach,
      corner[1] + direction[1] * reach,
    ]);
    const bisector = [d1[0] + d2[0], d1[1] + d2[1]];
    const bisectorLength = Math.hypot(...bisector);
    const centre = [
      corner[0] + (bisector[0] / bisectorLength) * (radius / Math.sin(angle / 2)),
      corner[1] + (bisector[1] / bisectorLength) * (radius / Math.sin(angle / 2)),
    ];
    const angleOf = (point) => Math.atan2(point[1] - centre[1], point[0] - centre[0]);
    // The arc runs counter-clockwise, and the short way round: from whichever
    // touch point is clockwise of the other one.
    const turn =
      (touch[0][0] - centre[0]) * (touch[1][1] - centre[1]) -
      (touch[0][1] - centre[1]) * (touch[1][0] - centre[0]);
    const startsAtFirst = turn > 0;
    const a0 = angleOf(touch[startsAtFirst ? 0 : 1]);
    let a1 = angleOf(touch[startsAtFirst ? 1 : 0]);
    while (a1 <= a0) {
      a1 += TWO_PI;
    }

    const next = copy(sketch);
    const id = nextId(next);
    ends.forEach((end, index) => {
      next.entities.find((entity) => entity.id === end.id)[end.end] = touch[index];
    });
    next.entities.push({ id, kind: "arc", c: centre, r: radius, a0, a1 });
    // What joined the corner now joins each line to its end of the arc.
    const cornerPoints = new Set(ends.map((end) => end.reference));
    // That includes a joint to the origin or to another shape's point: the line's end is no longer at the corner.
    next.constraints = next.constraints.filter(
      (constraint) =>
        !(
          constraint.kind === "coincident" &&
          (cornerPoints.has(constraint.a) || cornerPoints.has(constraint.b))
        ),
    );
    ends.forEach((end, index) => {
      const arcEnd = (startsAtFirst ? index === 0 : index === 1) ? "p0" : "p1";
      next.constraints.push({
        kind: "coincident",
        a: end.reference,
        b: `${id}.${arcEnd}`,
        id: nextConstraintId(next),
      });
      next.constraints.push({ kind: "tangent", a: end.id, b: id, id: nextConstraintId(next) });
    });
    next.constraints.push({ kind: "radius", e: id, value: radius, id: nextConstraintId(next) });
    return { sketch: next, id, reach };
  }

  /// A radius to offer for rounding the corner at `reference`: a quarter of the
  /// shorter line, to two places. Null when it is not a corner of two lines.
  function suggestFilletRadius(sketch, reference) {
    const ends = lineEndsAt(sketch, reference);
    if (ends.length !== 2 || ends[0].id === ends[1].id) {
      return null;
    }
    const lengths = ends.map((end) => {
      const line = sketch.entities.find((entity) => entity.id === end.id);
      return distance(line.p0, line.p1);
    });
    return Math.round((Math.min(...lengths) / 4) * 100) / 100;
  }

  /// Removes entities, and every constraint that mentions one.
  function deleteEntities(sketch, ids) {
    const gone = new Set(ids);
    const mentions = (constraint) =>
      ["a", "b", "e"].some((key) => constraint[key] && gone.has(entityIdOf(constraint[key])));
    return {
      entities: sketch.entities.filter((entity) => !gone.has(entity.id)),
      constraints: sketch.constraints.filter((constraint) => !mentions(constraint)),
    };
  }

  const indexOfConstraint = (sketch, id) =>
    sketch.constraints.findIndex((constraint) => constraint.id === id);

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

  const referencesOf = (constraint) =>
    ["a", "b", "e"].map((key) => constraint[key]).filter(Boolean);

  /// The entities a constraint acts on, for highlighting them. The origin and
  /// the sketch axes are not entities.
  function constraintEntities(sketch, constraint) {
    const known = new Set(sketch.entities.map((entity) => entity.id));
    return [...new Set(referencesOf(constraint).map(entityIdOf))].filter((id) => known.has(id));
  }

  /// "horizontal c3", or "distance 40.00 c9" for a dimension. Names a constraint in a message.
  function describeConstraint(constraint) {
    const value = typeof constraint.value === "number" ? ` ${constraint.value.toFixed(2)}` : "";
    return constraint.kind + value + (constraint.id ? ` ${constraint.id}` : "");
  }

  // --- Hit testing, in the sketch's own [u, v] ---------------------------

  function allPoints(sketch) {
    const own = sketch.entities.flatMap((entity) =>
      solver().POINTS[entity.kind].map((name) => ({
        reference: `${entity.id}.${name}`,
        uv: pointOf(sketch, `${entity.id}.${name}`),
      })),
    );
    // Last, so a shape drawn from the origin snaps to its own point on a tie.
    return [...own, { reference: ORIGIN, uv: [0, 0] }];
  }

  /// The nearest point within `radius`, skipping `exclude`, or null.
  function snapTarget(sketch, uv, radius, exclude) {
    let best = null;
    for (const point of allPoints(sketch)) {
      if (exclude?.includes(point.reference)) {
        continue;
      }
      const gap = distance(point.uv, uv);
      if (gap <= radius && (!best || gap < best.gap)) {
        best = { reference: point.reference, uv: point.uv, gap };
      }
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
      const t = lengthSquared
        ? Math.max(
            0,
            Math.min(1, ((uv[0] - ax) * (bx - ax) + (uv[1] - ay) * (by - ay)) / lengthSquared),
          )
        : 0;
      return [ax + t * (bx - ax), ay + t * (by - ay)];
    }
    const fromCentre = distance(uv, entity.c);
    const angle = fromCentre ? Math.atan2(uv[1] - entity.c[1], uv[0] - entity.c[0]) : 0;
    const onCircle = [
      entity.c[0] + entity.r * Math.cos(angle),
      entity.c[1] + entity.r * Math.sin(angle),
    ];
    if (entity.kind === "circle" || sweep(entity.a0, angle) <= sweep(entity.a0, entity.a1)) {
      return onCircle;
    }
    // Off the end of the arc: the nearer endpoint is the nearest part of it.
    const ends = ["p0", "p1"].map((name) =>
      pointOf({ entities: [entity] }, `${entity.id}.${name}`),
    );
    return distance(uv, ends[0]) <= distance(uv, ends[1]) ? ends[0] : ends[1];
  }

  const distanceToEntity = (entity, uv) => distance(uv, nearestOnEntity(entity, uv));

  /// A point on a line, circle or arc within `radius` of `uv`: {entity, uv}.
  function snapToCurve(sketch, uv, radius) {
    let best = null;
    for (const entity of sketch.entities) {
      const onIt = nearestOnEntity(entity, uv);
      const gap = distance(onIt, uv);
      if (gap <= radius && (!best || gap < best.gap)) {
        best = { entity: entity.id, kind: entity.kind, uv: onIt, gap };
      }
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
      if (gap <= radius && (!best || gap < best.gap)) {
        best = { ref: axis.ref, name: axis.name, uv: onIt, gap };
      }
    }
    return best;
  }

  /// Where a click lands: a point if one is near, else the nearer of a curve
  /// and a shown axis, else the raw position. `reference` or `curve` says what
  /// it snapped to, and joining a new shape to it adds the matching constraint.
  function snap(sketch, uv, radius, axes = []) {
    const point = snapTarget(sketch, uv, radius);
    if (point) {
      return { uv: point.uv, reference: point.reference, curve: null };
    }
    const curve = snapToCurve(sketch, uv, radius);
    const axis = snapToAxis(axes, uv, radius);
    if (axis && (!curve || axis.gap < curve.gap)) {
      return { uv: axis.uv, reference: null, curve: axis.ref, label: `On ${axis.name}` };
    }
    if (curve) {
      return { uv: curve.uv, reference: null, curve: curve.entity, label: `On ${curve.kind}` };
    }
    return { uv, reference: null, curve: null };
  }

  /// What is under `uv`: a point if one is within `radius`, else the nearest
  /// entity within it, else null.
  function hit(sketch, uv, radius) {
    const point = snapTarget(sketch, uv, radius);
    if (point) {
      return { kind: "point", reference: point.reference };
    }
    let best = null;
    for (const entity of sketch.entities) {
      const gap = distanceToEntity(entity, uv);
      if (gap <= radius && (!best || gap < best.gap)) {
        best = { kind: "entity", reference: entity.id, gap };
      }
    }
    return best;
  }

  /// "horizontal" or "vertical" when `to` is within `toleranceDegrees` of that
  /// direction from `from`, else null. Drawing a line nearly level makes it level.
  function alignment(from, to, toleranceDegrees) {
    const dx = Math.abs(to[0] - from[0]);
    const dy = Math.abs(to[1] - from[1]);
    if (dx + dy < 1e-9) {
      return null;
    }
    const tolerance = Math.tan((toleranceDegrees * Math.PI) / 180);
    if (dy <= dx * tolerance) {
      return "horizontal";
    }
    if (dx <= dy * tolerance) {
      return "vertical";
    }
    return null;
  }

  // --- Constraints and dimensions from a selection ------------------------

  const kindOf = (sketch, reference) =>
    sketch.entities.find((entity) => entity.id === entityIdOf(reference)).kind;
  const isLine = (sketch, reference) =>
    !isPointReference(reference) && kindOf(sketch, reference) === "line";
  const isCurve = (sketch, reference) =>
    !isPointReference(reference) && kindOf(sketch, reference) !== "line";

  /// The constraint `kind` applied to the selected references, or an Error
  /// that says what to select instead.
  function constraintFor(sketch, kind, selection) {
    const [first, second] = selection;
    switch (kind) {
      case "coincident":
        if (selection.length === 2 && selection.every(isPointReference)) {
          return { kind, a: first, b: second };
        }
        return fail("Select two points to make them coincident");
      case "horizontal":
      case "vertical":
        if (selection.length === 1 && isLine(sketch, first)) {
          return { kind, e: first };
        }
        if (selection.length === 2 && selection.every(isPointReference)) {
          return { kind, a: first, b: second };
        }
        return fail(
          `Select a line, or two points, to make ${kind === "horizontal" ? "level" : "upright"}`,
        );
      case "parallel":
      case "perpendicular":
        if (selection.length === 2 && selection.every((reference) => isLine(sketch, reference))) {
          return { kind, a: first, b: second };
        }
        return fail(`Select two lines to make them ${kind}`);
      case "equal":
        if (
          selection.length === 2 &&
          selection.every((reference) => !isPointReference(reference))
        ) {
          const kinds = selection.map((reference) => kindOf(sketch, reference));
          if (kinds[0] === "line" ? kinds[1] === "line" : kinds[1] !== "line") {
            return { kind, a: first, b: second };
          }
        }
        return fail("Select two lines, or two circles or arcs, to make them equal");
      case "tangent":
        if (
          selection.length === 2 &&
          selection.every((reference) => !isPointReference(reference)) &&
          selection.some((reference) => isCurve(sketch, reference))
        ) {
          return { kind, a: first, b: second };
        }
        return fail("Select a circle or arc and a line, circle or arc to make them tangent");
      case "point_on": {
        const [point, curve] =
          selection.length === 2 && isPointReference(first) ? [first, second] : [second, first];
        if (
          selection.length === 2 &&
          isPointReference(point) &&
          !isPointReference(curve) &&
          curve !== entityIdOf(point)
        ) {
          return { kind, a: point, b: curve };
        }
        return fail("Select a point and a line, circle or arc to put it on");
      }
      case "symmetric": {
        const points = selection.filter(isPointReference);
        const lines = selection.filter((reference) => isLine(sketch, reference));
        if (selection.length === 3 && points.length === 2 && lines.length === 1) {
          return { kind, a: points[0], b: points[1], e: lines[0] };
        }
        return fail("Select two points and a line to make the points mirror images across it");
      }
      case "fix":
        if (selection.length === 1 && first === ORIGIN) {
          return fail("The origin is already fixed");
        }
        if (selection.length === 1) {
          return { kind, e: first };
        }
        return fail("Select one point or shape to fix");
      default:
        return fail(`unknown constraint '${kind}'`);
    }
  }

  /// The dimension a selection can carry, with its current value so that
  /// accepting it unchanged moves nothing, and where to put its label. Null
  /// when the selection has none. Two points can be measured "aligned" (the
  /// straight distance, the default), "horizontal" or "vertical". Two lines
  /// that are both upright, or both level, are measured across: the gap
  /// between the edges. Any other two lines give their angle.
  function suggestDimension(sketch, selection, orientation = "aligned") {
    const midpoint = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    /// A distance along one axis from the lower of the two to the higher, so its value is positive.
    const across = (kind, aRef, bRef, a, b) => {
      const axis = kind === "distance_x" ? 0 : 1;
      const [first, second] = a[axis] <= b[axis] ? [aRef, bRef] : [bRef, aRef];
      return {
        constraint: { kind, a: first, b: second, value: Math.abs(b[axis] - a[axis]) },
        anchor: midpoint(a, b),
      };
    };
    if (selection.length === 1 && !isPointReference(selection[0])) {
      const entity = sketch.entities.find((item) => item.id === selection[0]);
      if (entity.kind === "line") {
        return {
          constraint: {
            kind: "distance",
            a: `${entity.id}.p0`,
            b: `${entity.id}.p1`,
            value: distance(entity.p0, entity.p1),
          },
          anchor: midpoint(entity.p0, entity.p1),
        };
      }
      if (entity.kind === "circle") {
        return {
          constraint: { kind: "diameter", e: entity.id, value: entity.r * 2 },
          anchor: entity.c,
        };
      }
      return { constraint: { kind: "radius", e: entity.id, value: entity.r }, anchor: entity.c };
    }
    if (selection.length === 2 && selection.every(isPointReference)) {
      const [a, b] = selection.map((reference) => pointOf(sketch, reference));
      if (orientation === "horizontal") {
        return across("distance_x", selection[0], selection[1], a, b);
      }
      if (orientation === "vertical") {
        return across("distance_y", selection[0], selection[1], a, b);
      }
      return {
        constraint: { kind: "distance", a: selection[0], b: selection[1], value: distance(a, b) },
        anchor: midpoint(a, b),
      };
    }
    if (selection.length === 2 && selection.every((reference) => isLine(sketch, reference))) {
      const [one, two] = selection.map((id) => sketch.entities.find((entity) => entity.id === id));
      const d1 = [one.p1[0] - one.p0[0], one.p1[1] - one.p0[1]];
      const d2 = [two.p1[0] - two.p0[0], two.p1[1] - two.p0[1]];
      const upright = (d) => Math.abs(d[0]) < 1e-6 * Math.max(1, Math.abs(d[1]));
      const level = (d) => Math.abs(d[1]) < 1e-6 * Math.max(1, Math.abs(d[0]));
      if (upright(d1) && upright(d2)) {
        return across("distance_x", `${one.id}.p0`, `${two.id}.p0`, one.p0, two.p0);
      }
      if (level(d1) && level(d2)) {
        return across("distance_y", `${one.id}.p0`, `${two.id}.p0`, one.p0, two.p0);
      }
      // planegcs measures the angle counter-clockwise from the first line to the second.
      const degrees =
        (Math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1]) * 180) / Math.PI;
      return {
        constraint: { kind: "angle", a: one.id, b: two.id, value: degrees },
        anchor: midpoint(one.p1, two.p1),
      };
    }
    return null;
  }

  /// Where a dimension constraint's label sits, in [u, v]. Null for constraints without one.
  function dimensionAnchor(sketch, constraint) {
    const suggestion = suggestDimension(
      sketch,
      constraint.kind === "distance" ||
        constraint.kind === "distance_x" ||
        constraint.kind === "distance_y"
        ? [constraint.a, constraint.b]
        : constraint.kind === "angle"
          ? [constraint.a, constraint.b]
          : constraint.e
            ? [constraint.e]
            : [],
    );
    if (!suggestion) {
      return null;
    }
    // A distance between a line's own two ends is drawn on the line.
    return suggestion.anchor;
  }

  /// A point on `reference` to hang a glyph on: a point is itself, a line is its
  /// middle, a circle or arc is a spot on its curve.
  function referenceAnchor(sketch, reference) {
    if (
      reference === ORIGIN ||
      (!reference.includes(".") && !sketch.entities.some((entity) => entity.id === reference))
    ) {
      return [0, 0];
    }
    if (isPointReference(reference)) {
      return pointOf(sketch, reference);
    }
    const entity = sketch.entities.find((item) => item.id === reference);
    if (entity.kind === "line") {
      return [(entity.p0[0] + entity.p1[0]) / 2, (entity.p0[1] + entity.p1[1]) / 2];
    }
    const angle =
      entity.kind === "arc"
        ? (entity.a0 + (entity.a1 > entity.a0 ? entity.a1 : entity.a1 + TWO_PI)) / 2
        : Math.PI / 4;
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
        if (constraint.e) {
          return [anchor(constraint.e)];
        }
        return [
          [
            (anchor(constraint.a)[0] + anchor(constraint.b)[0]) / 2,
            (anchor(constraint.a)[1] + anchor(constraint.b)[1]) / 2,
          ],
        ];
      case "fix":
        return [anchor(constraint.e)];
      default:
        return referencesOf(constraint).map(anchor);
    }
  }

  const sameSketch = (a, b) =>
    JSON.stringify([a.entities, a.constraints]) === JSON.stringify([b.entities, b.constraints]);

  /// A feature the document accepts: `plane` as the viewer holds it (a name or
  /// {origin, normal, x_dir}) plus the sketch.
  function toFeature(plane, sketch) {
    return { plane, entities: copy(sketch.entities), constraints: copy(sketch.constraints) };
  }

  const api = {
    emptySketch,
    nextId,
    addReference,
    mirrorEntities,
    addFillet,
    lineEndsAt,
    suggestFilletRadius,
    toggleConstruction,
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
    snapToCurve,
    distanceToEntity,
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
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.SketchModel = api;
  }
})(globalThis);
