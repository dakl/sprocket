// Trim, Extend and Offset: the sketch edits that cut, lengthen and copy curves.
//
// Pure like sketch-model.js: each function takes a sketch ({entities,
// constraints}) and returns a new one, or throws an Error that says why not.
// sketcher.js turns clicks into calls and hands the result to the solver.
//
// A cut end stays on what cut it (a point_on constraint). Anything that held
// the moved end, and any dimension on the shape, is dropped, because it no
// longer says what the shape is.
((root) => {
  const TWO_PI = 2 * Math.PI;
  const EPS = 1e-6;
  const model = () =>
    typeof module !== "undefined" && module.exports
      ? require("./sketch-model.js")
      : root.SketchModel;
  const solver = () =>
    typeof module !== "undefined" && module.exports
      ? require("./sketch-solver.js")
      : root.SketchSolver;
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const distance = ([ax, ay], [bx, by]) => Math.hypot(bx - ax, by - ay);
  const sweep = (from, to) => (((to - from) % TWO_PI) + TWO_PI) % TWO_PI;
  const fail = (message) => {
    throw new Error(message);
  };

  const VALUE_KINDS = ["distance", "distance_x", "distance_y", "radius", "diameter", "angle"];
  // Constraints on a whole shape that still hold for a shorter piece of it.
  const KEEPS = {
    line: ["horizontal", "vertical", "parallel", "perpendicular"],
    arc: ["radius", "diameter", "equal"],
    circle: ["radius", "diameter", "equal"],
  };

  const entityOf = (sketch, id) => sketch.entities.find((entity) => entity.id === id);
  const idOf = (reference) => reference.split(".")[0];
  const pointOf = (sketch, reference) => solver().pointOf(sketch, reference);
  const arcSpan = (arc) => Math.min(TWO_PI, Math.max(0, arc.a1 - arc.a0));
  const angleAt = (centre, point) => Math.atan2(point[1] - centre[1], point[0] - centre[0]);
  const onCircle = (centre, radius, angle) => [
    centre[0] + radius * Math.cos(angle),
    centre[1] + radius * Math.sin(angle),
  ];

  // --- Intersections ------------------------------------------------------

  function lineLine(a, b, c, d, infiniteFirst) {
    const [rx, ry] = [b[0] - a[0], b[1] - a[1]];
    const [sx, sy] = [d[0] - c[0], d[1] - c[1]];
    const cross = rx * sy - ry * sx;
    if (Math.abs(cross) < 1e-12) {
      return [];
    }
    const t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / cross;
    const u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / cross;
    const slack = EPS / Math.max(Math.hypot(sx, sy), EPS);
    if (u < -slack || u > 1 + slack) {
      return [];
    }
    if (
      !infiniteFirst &&
      (t < -EPS / Math.max(Math.hypot(rx, ry), EPS) ||
        t > 1 + EPS / Math.max(Math.hypot(rx, ry), EPS))
    ) {
      return [];
    }
    return [[a[0] + t * rx, a[1] + t * ry]];
  }

  /// The line a-b against the full circle. Points off the segment are dropped unless `infinite`.
  function lineCircle(a, b, centre, radius, infinite) {
    const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
    const [fx, fy] = [a[0] - centre[0], a[1] - centre[1]];
    const A = dx * dx + dy * dy;
    if (A < 1e-18) {
      return [];
    }
    const B = 2 * (fx * dx + fy * dy);
    const C = fx * fx + fy * fy - radius * radius;
    const discriminant = B * B - 4 * A * C;
    if (discriminant < -1e-9) {
      return [];
    }
    const root = Math.sqrt(Math.max(0, discriminant));
    const ts = root < 1e-7 ? [-B / (2 * A)] : [(-B - root) / (2 * A), (-B + root) / (2 * A)];
    const slack = EPS / Math.sqrt(A);
    return ts
      .filter((t) => infinite || (t >= -slack && t <= 1 + slack))
      .map((t) => [a[0] + t * dx, a[1] + t * dy]);
  }

  function circleCircle(c1, r1, c2, r2) {
    const gap = distance(c1, c2);
    if (gap < 1e-9 || gap > r1 + r2 + 1e-9 || gap < Math.abs(r1 - r2) - 1e-9) {
      return [];
    }
    const along = (r1 * r1 - r2 * r2 + gap * gap) / (2 * gap);
    const height = Math.sqrt(Math.max(0, r1 * r1 - along * along));
    const foot = [c1[0] + (along * (c2[0] - c1[0])) / gap, c1[1] + (along * (c2[1] - c1[1])) / gap];
    if (height < 1e-7) {
      return [foot];
    }
    const [ox, oy] = [(-(c2[1] - c1[1]) * height) / gap, ((c2[0] - c1[0]) * height) / gap];
    return [
      [foot[0] + ox, foot[1] + oy],
      [foot[0] - ox, foot[1] - oy],
    ];
  }

  const onArc = (arc, point) =>
    arc.kind !== "arc" ||
    sweep(arc.a0, angleAt(arc.c, point)) <= arcSpan(arc) + EPS / Math.max(arc.r, EPS);

  /// Where `first` meets `second`. With `extendFirst`, a line is taken as endless and an arc as its whole circle.
  function intersections(sketch, first, second, extendFirst) {
    if (first.kind === "line" && second.kind === "line") {
      return lineLine(first.p0, first.p1, second.p0, second.p1, extendFirst);
    }
    if (first.kind === "line") {
      return lineCircle(first.p0, first.p1, second.c, second.r, extendFirst).filter((point) =>
        onArc(second, point),
      );
    }
    if (second.kind === "line") {
      return lineCircle(second.p0, second.p1, first.c, first.r, false).filter(
        (point) => extendFirst || onArc(first, point),
      );
    }
    return circleCircle(first.c, first.r, second.c, second.r).filter(
      (point) => onArc(second, point) && (extendFirst || onArc(first, point)),
    );
  }

  // --- Which constraints survive a cut ----------------------------------------

  const mentions = (constraint, id) =>
    ["a", "b", "e"].some((key) => constraint[key] && idOf(constraint[key]) === id);
  const mentionsPoint = (constraint, reference) =>
    ["a", "b", "e"].some((key) => constraint[key] === reference);

  /// The constraints of `sketch` that still make sense once the shape `id` has
  /// lost the points `moved` (references like "e1.p1"): everything that does not
  /// touch the shape, constraints on its other points, and the whole-shape ones
  /// in KEEPS. A dimension that reads the shape's own ends goes.
  function surviving(sketch, id, moved) {
    const kind = entityOf(sketch, id).kind;
    return sketch.constraints.filter((constraint) => {
      if (!mentions(constraint, id)) {
        return true;
      }
      if (moved.some((reference) => mentionsPoint(constraint, reference))) {
        return false;
      }
      const wholeShape = ["a", "b", "e"].some((key) => constraint[key] === id);
      if (wholeShape) {
        return KEEPS[kind].includes(constraint.kind);
      }
      const ownPoints = ["a", "b", "e"].filter(
        (key) => constraint[key] && idOf(constraint[key]) === id,
      ).length;
      return (
        !(VALUE_KINDS.includes(constraint.kind) && ownPoints > 1) &&
        !(["horizontal", "vertical"].includes(constraint.kind) && ownPoints > 1)
      );
    });
  }

  function tie(sketch, kind, fields) {
    sketch.constraints.push({ kind, ...fields, id: model().nextConstraintId(sketch) });
  }

  // --- Trim ---------------------------------------------------------------------

  /// Where along `target` each of its crossings with the other shapes lies:
  /// [{at, point, cutter}] sorted by `at`, with `at` 0..1 on a line and 0..span
  /// of an arc, or an angle from `a0` round a circle. A crossing that sits on
  /// an end of the shape is not one.
  function crossings(sketch, target) {
    const found = [];
    for (const other of sketch.entities) {
      if (other.id === target.id) {
        continue;
      }
      for (const point of intersections(sketch, target, other, false)) {
        const at =
          target.kind === "line"
            ? parameterOnLine(target, point)
            : sweep(target.kind === "arc" ? target.a0 : 0, angleAt(target.c, point));
        found.push({ at, point, cutter: other.id });
      }
    }
    found.sort((x, y) => x.at - y.at);
    const length = target.kind === "line" ? distance(target.p0, target.p1) : target.r;
    const slack = EPS / Math.max(length, EPS);
    const top = target.kind === "line" ? 1 : target.kind === "arc" ? arcSpan(target) : TWO_PI;
    return found
      .filter((crossing, index) => index === 0 || crossing.at - found[index - 1].at > slack)
      .filter(
        (crossing) =>
          target.kind === "circle" || (crossing.at > slack && crossing.at < top - slack),
      );
  }

  function parameterOnLine(line, point) {
    const [dx, dy] = [line.p1[0] - line.p0[0], line.p1[1] - line.p0[1]];
    return ((point[0] - line.p0[0]) * dx + (point[1] - line.p0[1]) * dy) / (dx * dx + dy * dy);
  }

  /// Removes the piece of the line, arc or circle `id` that `uv` is on, between the
  /// shapes that cross it. With no crossing the whole shape goes. A piece in the
  /// middle splits the shape in two. Returns {sketch, ids} with the ids that now
  /// hold what was left, or throws.
  // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: a dense geometry routine: the circle, split and cut-back cases share the crossing search above them
  // biome-ignore lint/complexity/noExcessiveLinesPerFunction: a dense geometry routine: the circle, split and cut-back cases share the crossing search above them
  function trimEntity(sketch, id, uv) {
    const target = entityOf(sketch, id);
    if (!target) {
      fail("Nothing to trim there");
    }
    if (target.reference) {
      fail("A projected reference is locked, so it cannot be trimmed");
    }
    const hits = crossings(sketch, target);
    if (hits.length === 0) {
      const gone = model().deleteEntities(sketch, [id]);
      return { sketch: gone, ids: [] };
    }
    const next = copy(sketch);
    const entity = entityOf(next, id);
    const clickAt =
      target.kind === "line"
        ? Math.max(0, Math.min(1, parameterOnLine(target, uv)))
        : sweep(target.kind === "arc" ? target.a0 : 0, angleAt(target.c, uv));
    const before = [...hits].reverse().find((hit) => hit.at <= clickAt) || null;
    const after = hits.find((hit) => hit.at > clickAt) || null;

    if (target.kind === "circle") {
      if (hits.length < 2) {
        fail("A circle needs two crossings before part of it can be trimmed");
      }
      // The piece between two neighbouring crossings goes; the rest is an arc from the far end back round.
      const from = before || hits.at(-1);
      const to = after || hits[0];
      const start = angleAt(target.c, to.point);
      let end = angleAt(target.c, from.point);
      while (end <= start) {
        end += TWO_PI;
      }
      next.constraints = surviving(next, id, []);
      Object.assign(entity, { kind: "arc", a0: start, a1: end });
      tie(next, "point_on", { a: `${id}.p0`, b: to.cutter });
      tie(next, "point_on", { a: `${id}.p1`, b: from.cutter });
      return { sketch: next, ids: [id] };
    }

    if (before && after) {
      // Split: this id keeps the part up to `before`, a new shape takes the part from `after`.
      const secondId = model().nextId(next);
      const second = { id: secondId, kind: target.kind };
      if (target.construction) {
        second.construction = true;
      }
      if (target.kind === "line") {
        Object.assign(second, { p0: after.point, p1: [...target.p1] });
      } else {
        Object.assign(second, {
          c: [...target.c],
          r: target.r,
          a0: angleAt(target.c, after.point),
          a1: target.a1,
        });
      }
      if (target.kind === "arc") {
        while (second.a1 <= second.a0) {
          second.a1 += TWO_PI;
        }
      }
      const oldEnd = `${id}.p1`;
      const carried = next.constraints.filter((constraint) => mentionsPoint(constraint, oldEnd));
      const copies = next.constraints.filter(
        (constraint) =>
          KEEPS[target.kind].includes(constraint.kind) &&
          ["a", "b", "e"].some((key) => constraint[key] === id) &&
          target.kind === "line",
      );
      next.constraints = surviving(next, id, [oldEnd]);
      if (target.kind === "line") {
        entity.p1 = before.point;
      } else {
        entity.a1 = target.a0 + before.at;
      }
      next.entities.push(second);
      for (const constraint of carried) {
        const relocated = { ...constraint, id: model().nextConstraintId(next) };
        for (const key of ["a", "b", "e"]) {
          if (relocated[key] === oldEnd) {
            relocated[key] = `${secondId}.p1`;
          }
        }
        // A dimension or a level that read both ends of the shape does not carry over.
        const ownRefs = ["a", "b", "e"].filter(
          (key) => constraint[key] && idOf(constraint[key]) === id,
        ).length;
        if (ownRefs < 2) {
          next.constraints.push(relocated);
        }
      }
      for (const constraint of copies) {
        const duplicate = { ...constraint, id: model().nextConstraintId(next) };
        for (const key of ["a", "b", "e"]) {
          if (duplicate[key] === id) {
            duplicate[key] = secondId;
          }
        }
        next.constraints.push(duplicate);
      }
      if (target.kind === "arc") {
        tie(next, "coincident", { a: `${id}.c`, b: `${secondId}.c` });
        tie(next, "equal", { a: id, b: secondId });
      }
      tie(next, "point_on", { a: `${id}.p1`, b: before.cutter });
      tie(next, "point_on", { a: `${secondId}.p0`, b: after.cutter });
      return { sketch: next, ids: [id, secondId] };
    }
    if (!before && !after) {
      fail("Nothing to trim there");
    }
    // One end is cut back to a crossing: `after` cuts the start, `before` cuts the end.
    const end = after ? "p0" : "p1";
    const cut = after || before;
    next.constraints = surviving(next, id, [`${id}.${end}`]);
    if (target.kind === "line") {
      entity[end] = cut.point;
    } else if (end === "p0") {
      entity.a0 = angleAt(target.c, cut.point);
      entity.a1 = target.a1;
      while (entity.a1 <= entity.a0) {
        entity.a1 += TWO_PI;
      }
    } else {
      entity.a1 = target.a0 + cut.at;
    }
    tie(next, "point_on", { a: `${id}.${end}`, b: cut.cutter });
    return { sketch: next, ids: [id] };
  }

  // --- Extend -------------------------------------------------------------------

  /// Lengthens the end of the line or arc `id` that is nearer `uv`, to the first
  /// shape in its way. Throws when nothing is there to reach.
  // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: a dense geometry routine: each shape kind finds its nearest crossing the same way
  function extendEntity(sketch, id, uv) {
    const target = entityOf(sketch, id);
    if (!target || target.kind === "circle") {
      fail("Click a line or an arc");
    }
    if (target.reference) {
      fail("A projected reference is locked, so it cannot be extended");
    }
    const ends = ["p0", "p1"].map((name) => pointOf(sketch, `${id}.${name}`));
    const end = distance(uv, ends[0]) <= distance(uv, ends[1]) ? "p0" : "p1";
    const best = { gap: Number.POSITIVE_INFINITY, point: null, cutter: null, angle: 0 };
    for (const other of sketch.entities) {
      if (other.id === id) {
        continue;
      }
      for (const point of intersections(sketch, target, other, true)) {
        let gap;
        if (target.kind === "line") {
          const t = parameterOnLine(target, point);
          const length = distance(target.p0, target.p1);
          gap = (end === "p1" ? t - 1 : -t) * length;
        } else {
          const angle = angleAt(target.c, point);
          gap = (end === "p1" ? sweep(target.a1, angle) : sweep(angle, target.a0)) * target.r;
          // Past the far end of the arc is the arc itself, not an extension.
          if (gap > (TWO_PI - arcSpan(target)) * target.r - EPS) {
            gap = Number.POSITIVE_INFINITY;
          }
        }
        if (gap > EPS && gap < best.gap) {
          Object.assign(best, { gap, point, cutter: other.id });
        }
      }
    }
    if (!best.point) {
      fail("Nothing in the way: there is no shape for it to reach");
    }
    const next = copy(sketch);
    const entity = entityOf(next, id);
    next.constraints = surviving(next, id, [`${id}.${end}`]);
    if (target.kind === "line") {
      entity[end] = best.point;
    } else if (end === "p0") {
      entity.a0 = angleAt(target.c, best.point);
      entity.a1 = target.a1;
      while (entity.a1 <= entity.a0) {
        entity.a1 += TWO_PI;
      }
    } else {
      entity.a1 = angleAt(target.c, best.point);
      while (entity.a1 <= entity.a0) {
        entity.a1 += TWO_PI;
      }
    }
    tie(next, "point_on", { a: `${id}.${end}`, b: best.cutter });
    return { sketch: next, id, end };
  }

  // --- Offset -------------------------------------------------------------------

  function endsOf(entity) {
    if (entity.kind === "line") {
      return [entity.p0, entity.p1];
    }
    if (entity.kind === "arc") {
      return [onCircle(entity.c, entity.r, entity.a0), onCircle(entity.c, entity.r, entity.a1)];
    }
    return [];
  }

  /// The shapes joined end to end with `startId`, in order, each as {id, reversed}
  /// (walked from its p1 to its p0), and whether the chain closes on itself.
  function chainOf(sketch, startId) {
    const start = entityOf(sketch, startId);
    if (!start || start.reference) {
      fail("Click a line, an arc or a circle to offset");
    }
    if (start.kind === "circle") {
      return { links: [{ id: startId, reversed: false }], closed: false };
    }
    const candidates = sketch.entities.filter(
      (entity) =>
        entity.kind !== "circle" &&
        !entity.reference &&
        Boolean(entity.construction) === Boolean(start.construction),
    );
    const used = new Set([startId]);
    const walk = (from) => {
      const links = [];
      let at = from;
      for (;;) {
        const joined = candidates.filter(
          (entity) => !used.has(entity.id) && endsOf(entity).some((end) => distance(end, at) < EPS),
        );
        if (joined.length !== 1) {
          return links;
        }
        const entity = joined[0];
        const [first, last] = endsOf(entity);
        const reversed = distance(last, at) < EPS && distance(first, at) >= EPS;
        used.add(entity.id);
        links.push({ id: entity.id, reversed });
        at = reversed ? first : last;
      }
    };
    const [startFirst, startLast] = endsOf(start);
    const forward = walk(startLast);
    const lastLink = forward.at(-1);
    let closed = false;
    if (lastLink) {
      const [first, last] = endsOf(entityOf(sketch, lastLink.id));
      const exit = lastLink.reversed ? first : last;
      closed = distance(exit, startFirst) < EPS;
    }
    const backward = closed ? [] : walk(startFirst);
    const links = [
      ...backward.reverse().map((link) => ({ id: link.id, reversed: !link.reversed })),
      { id: startId, reversed: false },
      ...forward,
    ];
    return { links, closed };
  }

  /// Which side of `startId` the point `uv` is on: 1 for the left of its walk
  /// (inside an arc or a circle), -1 for the right. 0 when it is on the shape.
  function sideOf(sketch, startId, uv) {
    const entity = entityOf(sketch, startId);
    if (entity.kind === "line") {
      const side =
        (entity.p1[0] - entity.p0[0]) * (uv[1] - entity.p0[1]) -
        (entity.p1[1] - entity.p0[1]) * (uv[0] - entity.p0[0]);
      return Math.abs(side) < 1e-12 ? 0 : Math.sign(side);
    }
    const gap = distance(uv, entity.c) - entity.r;
    return Math.abs(gap) < 1e-12 ? 0 : gap < 0 ? 1 : -1;
  }

  /// A copy of the chain through `startId`, moved `amount` to the left of its walk
  /// (negative: the right). The corners are where the moved shapes meet, so a
  /// rectangle moves out to a bigger one. Copies are parallel to what they copy
  /// (arcs and circles keep the centre and get a radius), and are not dimensioned
  /// from the originals. Returns {sketch, ids} or throws.
  function offsetChain(sketch, startId, amount) {
    if (!Number.isFinite(amount) || Math.abs(amount) < EPS) {
      fail("The offset must not be zero");
    }
    const { links, closed } = chainOf(sketch, startId);
    const oriented = links.map((link) => {
      const entity = entityOf(sketch, link.id);
      const [first, last] = endsOf(entity);
      return {
        link,
        entity,
        entry: link.reversed ? last : first,
        exit: link.reversed ? first : last,
      };
    });
    // The curve each shape moves to: {kind: "line", a, b} or {kind: "circle", c, r}.
    const curves = oriented.map(({ entity, link }) => {
      if (entity.kind === "line") {
        const [a, b] = link.reversed ? [entity.p1, entity.p0] : [entity.p0, entity.p1];
        const length = distance(a, b);
        if (length < EPS) {
          fail("A line with no length cannot be offset");
        }
        const normal = [-(b[1] - a[1]) / length, (b[0] - a[0]) / length];
        return {
          kind: "line",
          a: [a[0] + normal[0] * amount, a[1] + normal[1] * amount],
          b: [b[0] + normal[0] * amount, b[1] + normal[1] * amount],
        };
      }
      // Walked counter-clockwise the centre is on the left, so a left move shrinks the radius.
      const r = entity.r + (link.reversed ? amount : -amount);
      if (r < EPS) {
        fail("The offset is bigger than the radius of an arc in it");
      }
      return { kind: "circle", c: [...entity.c], r };
    });
    const asEntity = (curve, id) =>
      curve.kind === "line"
        ? { id, kind: "line", p0: curve.a, p1: curve.b }
        : { id, kind: "circle", c: curve.c, r: curve.r };
    // Natural end of each moved curve, where a corner has no partner or the curves only touch.
    const naturalExit = (index) => {
      if (!oriented[index].exit) {
        return null;
      }
      const curve = curves[index];
      return curve.kind === "line"
        ? curve.b
        : onCircle(curve.c, curve.r, angleAt(oriented[index].entity.c, oriented[index].exit));
    };
    const naturalEntry = (index) => {
      if (!oriented[index].entry) {
        return null;
      }
      const curve = curves[index];
      return curve.kind === "line"
        ? curve.a
        : onCircle(curve.c, curve.r, angleAt(oriented[index].entity.c, oriented[index].entry));
    };
    const corner = (index) => {
      const next = (index + 1) % curves.length;
      const [first, second] = [curves[index], curves[next]];
      const guess = [
        (naturalExit(index)[0] + naturalEntry(next)[0]) / 2,
        (naturalExit(index)[1] + naturalEntry(next)[1]) / 2,
      ];
      if (first.kind === "line" && second.kind === "line") {
        return lineInfinite(first, second) || guess;
      }
      // The endless line goes first, so it is not cut to its length.
      const [line, other] =
        first.kind === "line" || second.kind !== "line" ? [first, second] : [second, first];
      let best = null;
      for (const point of intersections(
        { entities: [] },
        asEntity(line, "x"),
        asEntity(other, "y"),
        true,
      )) {
        if (!best || distance(point, guess) < distance(best, guess)) {
          best = point;
        }
      }
      // Two curves that only touch, as a line and the arc it is tangent to do, have no crossing to find.
      return best && distance(best, guess) < Math.abs(amount) * 3 + EPS ? best : guess;
    };

    const joints = [];
    const count = curves.length;
    for (let index = 0; index < (closed ? count : count - 1); index++) {
      joints[index] = corner(index);
    }
    const entryPoint = (index) =>
      index === 0 && !closed ? naturalEntry(0) : joints[(index - 1 + count) % count];
    const exitPoint = (index) =>
      index === count - 1 && !closed ? naturalExit(index) : joints[index];

    const next = copy(sketch);
    const created = [];
    oriented.forEach(({ entity, link }, index) => {
      const id = model().nextId(next);
      const curve = curves[index];
      const fresh = { id, kind: entity.kind };
      if (entity.construction) {
        fresh.construction = true;
      }
      const entry = entity.kind === "circle" ? null : entryPoint(index);
      const exit = entity.kind === "circle" ? null : exitPoint(index);
      let inName = "p0";
      let outName = "p1";
      if (entity.kind === "line") {
        Object.assign(fresh, { p0: entry, p1: exit });
      } else if (entity.kind === "circle") {
        Object.assign(fresh, { c: curve.c, r: curve.r });
      } else {
        const [a, b] = [angleAt(curve.c, entry), angleAt(curve.c, exit)];
        // Walked clockwise, the stored (counter-clockwise) arc starts at the exit.
        if (link.reversed) {
          Object.assign(fresh, { c: curve.c, r: curve.r, a0: b, a1: a }),
            (inName = "p1"),
            (outName = "p0");
        } else {
          Object.assign(fresh, { c: curve.c, r: curve.r, a0: a, a1: b });
        }
        while (fresh.a1 <= fresh.a0) {
          fresh.a1 += TWO_PI;
        }
      }
      next.entities.push(fresh);
      created.push({ id, inName, outName, from: entity.id });
    });
    created.forEach((item, index) => {
      const entity = entityOf(next, item.id);
      if (entity.kind === "line") {
        tie(next, "parallel", { a: item.from, b: item.id });
      } else {
        tie(next, "coincident", { a: `${item.from}.c`, b: `${item.id}.c` });
        tie(next, "radius", { e: item.id, value: Math.round(entity.r * 1e6) / 1e6 });
      }
      const following = created[index + 1] || (closed ? created[0] : null);
      if (!following || created.length === 1) {
        return;
      }
      tie(next, "coincident", {
        a: `${item.id}.${item.outName}`,
        b: `${following.id}.${following.inName}`,
      });
      // A smooth join stays smooth.
      const smooth = sketch.constraints.some(
        (constraint) =>
          constraint.kind === "tangent" &&
          [item.from, following.from].every((id) => [constraint.a, constraint.b].includes(id)),
      );
      if (smooth) {
        tie(next, "tangent", { a: item.id, b: following.id });
      }
    });
    return { sketch: next, ids: created.map((item) => item.id) };
  }

  /// Where two endless lines {a, b} meet, or null when they are parallel.
  function lineInfinite(first, second) {
    const [rx, ry] = [first.b[0] - first.a[0], first.b[1] - first.a[1]];
    const [sx, sy] = [second.b[0] - second.a[0], second.b[1] - second.a[1]];
    const cross = rx * sy - ry * sx;
    if (Math.abs(cross) < 1e-9 * Math.hypot(rx, ry) * Math.hypot(sx, sy)) {
      return null;
    }
    const t = ((second.a[0] - first.a[0]) * sy - (second.a[1] - first.a[1]) * sx) / cross;
    return [first.a[0] + t * rx, first.a[1] + t * ry];
  }

  const api = { trimEntity, extendEntity, offsetChain, chainOf, sideOf, intersections };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.SketchTools = api;
  }
})(globalThis);
