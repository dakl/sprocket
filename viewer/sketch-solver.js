// Runs a sketch's constraints through planegcs, FreeCAD's 2D solver.
//
// A sketch here is what the document stores: {entities, constraints}, with each
// entity a line (p0, p1), an arc (c, r, a0, a1) or a circle (c, r), and each
// constraint naming entities or points as "e1" or "e1.p0". This module turns
// that into planegcs primitives, solves, and reads the moved numbers back. It
// holds no state and knows nothing about the DOM or three.js, so
// viewer/test/sketch-solver.test.js runs it under node against the real solver.
//
// The solver itself comes in as `gcs`, one long-lived instance from
// `Planegcs.createSolver()` (see vendor/planegcs.js). It is expensive to make
// and cheap to reuse.
(function (root) {
  const TWO_PI = 2 * Math.PI;
  const POINTS = { line: ["p0", "p1"], arc: ["c", "p0", "p1"], circle: ["c"] };
  // planegcs SolveStatus: Success 0, Converged 1, Failed 2, SuccessfulSolutionInvalid 3.
  const SOLVED = [0, 1];

  function fail(message) {
    throw new Error(message);
  }

  function entityById(sketch, id) {
    const entity = sketch.entities.find((item) => item.id === id);
    if (!entity) fail("no entity '" + id + "'");
    return entity;
  }

  /// The [u, v] of a point reference such as "e1.p0". An arc's p0 and p1 are
  /// derived from its centre, radius and angles.
  function pointOf(sketch, reference) {
    const [id, name] = reference.split(".");
    const entity = entityById(sketch, id);
    if (entity.kind === "arc" && name !== "c") {
      const angle = name === "p0" ? entity.a0 : entity.a1;
      return [entity.c[0] + entity.r * Math.cos(angle), entity.c[1] + entity.r * Math.sin(angle)];
    }
    return entity[name];
  }

  function primitivesFor(sketch) {
    const primitives = [];
    const point = (id, [x, y]) => primitives.push({ id, type: "point", x, y, fixed: false });

    // Points before the shapes that use them: planegcs wants lower ids first.
    for (const entity of sketch.entities) {
      for (const name of POINTS[entity.kind]) point(entity.id + "." + name, pointOf(sketch, entity.id + "." + name));
    }
    for (const entity of sketch.entities) {
      const at = (name) => entity.id + "." + name;
      if (entity.kind === "line") {
        primitives.push({ id: entity.id, type: "line", p1_id: at("p0"), p2_id: at("p1") });
      } else if (entity.kind === "circle") {
        primitives.push({ id: entity.id, type: "circle", c_id: at("c"), radius: entity.r });
      } else {
        // planegcs runs an arc counter-clockwise from start to end and wants
        // end_angle above start_angle.
        let end = entity.a1;
        while (end <= entity.a0) end += TWO_PI;
        primitives.push({
          id: entity.id,
          type: "arc",
          c_id: at("c"),
          radius: entity.r,
          start_angle: entity.a0,
          end_angle: end,
          start_id: at("p0"),
          end_id: at("p1"),
        });
        primitives.push({ id: entity.id + "#rules", type: "arc_rules", a_id: entity.id });
      }
    }
    return primitives;
  }

  /// A curve reference is an entity id. `kinds` maps the pair of kinds a
  /// constraint joins to the planegcs constraint that joins them.
  function curvePair(sketch, constraint, table) {
    const a = entityById(sketch, constraint.a);
    const b = entityById(sketch, constraint.b);
    const key = [a.kind, b.kind].join("-");
    const flipped = [b.kind, a.kind].join("-");
    if (table[key]) return table[key](a.id, b.id);
    if (table[flipped]) return table[flipped](b.id, a.id);
    return fail(constraint.kind + " cannot join a " + a.kind + " and a " + b.kind);
  }

  const TANGENT = {
    "line-circle": (l, c) => ({ type: "tangent_lc", l_id: l, c_id: c }),
    "line-arc": (l, a) => ({ type: "tangent_la", l_id: l, a_id: a }),
    "circle-circle": (c1, c2) => ({ type: "tangent_cc", c1_id: c1, c2_id: c2 }),
    "circle-arc": (c, a) => ({ type: "tangent_ca", c_id: c, a_id: a }),
    "arc-arc": (a1, a2) => ({ type: "tangent_aa", a1_id: a1, a2_id: a2 }),
  };

  const EQUAL = {
    "line-line": (l1, l2) => ({ type: "equal_length", l1_id: l1, l2_id: l2 }),
    "circle-circle": (c1, c2) => ({ type: "equal_radius_cc", c1_id: c1, c2_id: c2 }),
    // planegcs names the circle's field c1_id here and the arc's a2_id.
    "circle-arc": (c, a) => ({ type: "equal_radius_ca", c1_id: c, a2_id: a }),
    "arc-arc": (a1, a2) => ({ type: "equal_radius_aa", a1_id: a1, a2_id: a2 }),
  };

  function requireKind(sketch, id, ...kinds) {
    const entity = entityById(sketch, id);
    if (!kinds.includes(entity.kind)) fail("expected " + kinds.join(" or ") + " but " + id + " is a " + entity.kind);
    return entity;
  }

  /// planegcs constraint(s) for one document constraint.
  function constraintFor(sketch, constraint) {
    const { kind } = constraint;
    switch (kind) {
      case "coincident":
        return [{ type: "p2p_coincident", p1_id: constraint.a, p2_id: constraint.b }];
      case "horizontal":
      case "vertical": {
        if (constraint.e) {
          requireKind(sketch, constraint.e, "line");
          return [{ type: kind + "_l", l_id: constraint.e }];
        }
        return [{ type: kind + "_pp", p1_id: constraint.a, p2_id: constraint.b }];
      }
      case "parallel":
        requireKind(sketch, constraint.a, "line");
        requireKind(sketch, constraint.b, "line");
        return [{ type: "parallel", l1_id: constraint.a, l2_id: constraint.b }];
      case "perpendicular":
        requireKind(sketch, constraint.a, "line");
        requireKind(sketch, constraint.b, "line");
        return [{ type: "perpendicular_ll", l1_id: constraint.a, l2_id: constraint.b }];
      case "tangent":
        return [curvePair(sketch, constraint, TANGENT)];
      case "equal":
        return [curvePair(sketch, constraint, EQUAL)];
      case "fix": {
        // A point is fixed where it is. A whole entity fixes all its points.
        const references = constraint.e.includes(".")
          ? [constraint.e]
          : POINTS[entityById(sketch, constraint.e).kind].map((name) => constraint.e + "." + name);
        const pins = references.flatMap((reference) => {
          const [x, y] = pointOf(sketch, reference);
          return [
            { type: "coordinate_x", p_id: reference, x },
            { type: "coordinate_y", p_id: reference, y },
          ];
        });
        // Fixing a whole circle holds its size too, as Fusion does. An arc's
        // size follows from its three fixed points.
        const whole = constraint.e.includes(".") ? null : entityById(sketch, constraint.e);
        if (whole && whole.kind === "circle") pins.push({ type: "circle_radius", c_id: whole.id, radius: whole.r });
        return pins;
      }
      case "distance":
        return [{ type: "p2p_distance", p1_id: constraint.a, p2_id: constraint.b, distance: constraint.value }];
      case "radius": {
        const entity = requireKind(sketch, constraint.e, "circle", "arc");
        return [{ type: entity.kind + "_radius", [entity.kind === "circle" ? "c_id" : "a_id"]: entity.id, radius: constraint.value }];
      }
      case "diameter": {
        const entity = requireKind(sketch, constraint.e, "circle", "arc");
        return [{ type: entity.kind + "_diameter", [entity.kind === "circle" ? "c_id" : "a_id"]: entity.id, diameter: constraint.value }];
      }
      case "angle":
        requireKind(sketch, constraint.a, "line");
        requireKind(sketch, constraint.b, "line");
        return [{ type: "l2l_angle_ll", l1_id: constraint.a, l2_id: constraint.b, angle: (constraint.value * Math.PI) / 180 }];
      default:
        return fail("unknown constraint kind '" + kind + "'");
    }
  }

  /// Pushes the sketch into `gcs`. `extra` is more planegcs constraints, used
  /// by entityStates to probe a single point. `pinned` lists point ids the
  /// solver must not move.
  function load(gcs, sketch, extra, pinned) {
    gcs.clear_data();
    const owner = {};
    const constraints = [];
    sketch.constraints.forEach((constraint, index) => {
      constraintFor(sketch, constraint).forEach((planegcs, part) => {
        const id = "#" + index + "." + part;
        owner[id] = index;
        constraints.push({ id, ...planegcs });
      });
    });
    (extra || []).forEach((planegcs, index) => constraints.push({ id: "probe" + index, ...planegcs }));
    const primitives = primitivesFor(sketch);
    for (const primitive of primitives) {
      if (primitive.type === "point" && pinned && pinned.includes(primitive.id)) primitive.fixed = true;
    }
    gcs.push_primitives_and_params([...primitives, ...constraints]);
    return owner;
  }

  function readBack(gcs, sketch) {
    const byId = new Map(gcs.sketch_index.get_primitives().map((primitive) => [primitive.id, primitive]));
    const moved = JSON.parse(JSON.stringify(sketch));
    for (const entity of moved.entities) {
      const at = (name) => byId.get(entity.id + "." + name);
      if (entity.kind === "line") {
        entity.p0 = [at("p0").x, at("p0").y];
        entity.p1 = [at("p1").x, at("p1").y];
      } else if (entity.kind === "circle") {
        entity.c = [at("c").x, at("c").y];
        entity.r = byId.get(entity.id).radius;
      } else {
        const arc = byId.get(entity.id);
        entity.c = [at("c").x, at("c").y];
        entity.r = arc.radius;
        entity.a0 = arc.start_angle;
        entity.a1 = arc.end_angle;
        while (entity.a1 <= entity.a0) entity.a1 += TWO_PI;
      }
    }
    return moved;
  }

  function named(ids, owner) {
    return [...new Set(ids.map((id) => owner[id]).filter((index) => index !== undefined))].sort((a, b) => a - b);
  }

  /// Moves a point, or a whole entity, to where the pointer is before solving.
  /// The solver then finds the nearest arrangement that still obeys the
  /// constraints, so a dragged corner of a dimensioned rectangle stays put in
  /// the direction a dimension forbids instead of tearing the sketch.
  function dragged(sketch, drag) {
    const moved = JSON.parse(JSON.stringify(sketch));
    const [id, name] = drag.ref.split(".");
    const entity = entityById(moved, id);
    const delta = drag.delta;
    const shift = (uv) => [uv[0] + delta[0], uv[1] + delta[1]];
    let held;
    if (name === undefined) {
      held = entity.kind === "line" ? [id + ".p0", id + ".p1"] : [id + ".c"];
      if (entity.kind === "line") {
        entity.p0 = shift(entity.p0);
        entity.p1 = shift(entity.p1);
      } else {
        entity.c = shift(entity.c);
      }
    } else if (entity.kind === "line" || name === "c") {
      held = [drag.ref];
      entity[name] = shift(entity[name]);
    } else {
      // Dragging an arc end swings it round the centre.
      const to = shift(pointOf(sketch, drag.ref));
      entity[name === "p0" ? "a0" : "a1"] = Math.atan2(to[1] - entity.c[1], to[0] - entity.c[0]);
      held = [drag.ref];
    }
    return { moved, held };
  }

  /// Solves `sketch`. Returns {ok, sketch, dof, conflicting, redundant}.
  /// `sketch` is the moved copy when ok, and the input untouched when not, so
  /// a caller that refuses a bad dimension has nothing to undo. `conflicting`
  /// and `redundant` are indices into sketch.constraints.
  function solve(gcs, sketch, options) {
    const drag = options && options.drag ? dragged(sketch, options.drag) : null;
    const start = drag ? drag.moved : sketch;

    // A dragged point is held where the pointer is and everything else gives
    // way. Left free, the solver spreads the move over every point the
    // constraints tie together, and a corner then lags behind the cursor. If
    // holding it makes the sketch unsolvable, the point was dragged somewhere a
    // dimension forbids, so fall back to the nearest solution with it free.
    for (const pinned of drag ? [drag.held, null] : [null]) {
      const owner = load(gcs, start, null, pinned);
      const status = gcs.solve();
      const conflicting = named(gcs.get_gcs_conflicting_constraints(), owner);
      const redundant = named(gcs.get_gcs_redundant_constraints(), owner);
      if (SOLVED.includes(status) && !conflicting.length) {
        gcs.apply_solution();
        return { ok: true, sketch: readBack(gcs, start), dof: gcs.gcs.dof(), conflicting, redundant };
      }
      if (!pinned) return { ok: false, sketch, dof: null, conflicting, redundant };
    }
  }

  /// Which entities are fully constrained: nothing about them can still move.
  /// A point is determined when pinning it where it sits removes no freedom,
  /// which is to say when the solver calls the pin redundant. Fusion 360 draws
  /// such entities in black and the rest in blue.
  function entityStates(gcs, sketch) {
    const solved = solve(gcs, sketch);
    if (!solved.ok) return { dof: null, states: {} };
    const base = solved.sketch;
    const baseDof = solved.dof;

    const pinned = (reference) => {
      const [x, y] = pointOf(base, reference);
      return [
        { type: "coordinate_x", p_id: reference, x },
        { type: "coordinate_y", p_id: reference, y },
      ];
    };
    const removesNothing = (extra) => {
      load(gcs, base, extra);
      gcs.solve();
      return gcs.gcs.dof() === baseDof;
    };

    const states = {};
    for (const entity of base.entities) {
      let determined = POINTS[entity.kind].every((name) => removesNothing(pinned(entity.id + "." + name)));
      if (determined && entity.kind === "circle") {
        determined = removesNothing([{ type: "circle_radius", c_id: entity.id, radius: entity.r }]);
      }
      states[entity.id] = determined ? "fixed" : "free";
    }
    return { dof: baseDof, states };
  }

  const api = { solve, entityStates, pointOf, POINTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SketchSolver = api;
})(typeof window !== "undefined" ? window : globalThis);
