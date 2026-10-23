// The sketch editor: drawing tools, dimensions, constraints and dragging, on a
// plane in the 3D view.
//
// The rules live elsewhere. sketch-model.js decides what a click adds and
// what a selection can be constrained to, and sketch-solver.js moves the
// geometry to obey the constraints. This file turns pointer and key events
// into calls to those two, and draws the result. It reaches the rest of the
// viewer only through the `host` object it is created with.
//
// A new sketch lives in memory until Finish, and one edit goes to the engine at
// the end. A sketch that is already in the document is synced as you work: each
// change goes to the engine as one update, and a change made somewhere else, by
// an agent, is adopted here. Undo and redo are local to the sketcher either way.
((root) => {
  const TWO_PI = 2 * Math.PI;
  const SNAP_PIXELS = 10;
  const ALIGN_DEGREES = 4;
  const DRAG_PIXELS = 4;
  const COLOURS = {
    free: 0x4d_a3_ff,
    fixed: 0xe8_e8_e8,
    selected: 0xff_aa_00,
    hover: 0xff_dd_55,
    preview: 0x5b_e0_a0,
    origin: 0xff_6b_6b,
    reference: 0xc7_7d_ff,
  };
  const TOOL_KEYS = {
    s: "select",
    l: "line",
    r: "rectangle",
    c: "circle",
    a: "arc",
    d: "dimension",
    p: "project",
    f: "fillet",
    t: "trim",
    e: "extend",
    o: "offset",
  };
  const HINTS = {
    select: "Click to select, drag to move. Delete removes.",
    line: "Click to start a line, click again to place its end. Esc ends the chain.",
    rectangle: "Click a corner, then the opposite one",
    circle: "Click the centre, then a point on the circle",
    arc: "Click the start, the end, then a point the arc passes through",
    dimension: "Click a line, a circle, two points or two lines",
    fillet: "Click a corner where two lines meet, then type the radius",
    project:
      "Click an edge of another sketch or of the part to project it here as a locked reference",
    trim: "Click the piece of a line, arc or circle to remove, between the shapes that cross it",
    extend: "Click near the end of a line or arc to run it on to the next shape",
    offset: "Click a line, arc or circle, move to the side, then type a distance and press Enter",
  };
  // In the dimension box, between two points: which way to measure.
  const ORIENTATIONS = { a: "aligned", h: "horizontal", v: "vertical" };
  const PLACING = {
    line: ["Click the line's end"],
    rectangle: ["Click the opposite corner"],
    circle: ["Click a point on the circle"],
    arc: ["Click the arc's end", "Click a point the arc passes through"],
  };
  const GLYPHS = {
    coincident: "•",
    point_on: "∘",
    horizontal: "—",
    vertical: "|",
    parallel: "∥",
    perpendicular: "⟂",
    tangent: "◠",
    equal: "=",
    fix: "▣",
    symmetric: "⇋",
  };
  const UNDO_LIMIT = 100;
  const STALE = /based on revision/;
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const CONSTRAINTS = [
    "coincident",
    "point_on",
    "horizontal",
    "vertical",
    "parallel",
    "perpendicular",
    "tangent",
    "equal",
    "fix",
    "symmetric",
  ];

  function createSketcher(host) {
    const { THREE, scene, camera, renderer, model, solver } = host;
    const group = new THREE.Group();
    scene.add(group);
    const ui = host.ui;

    let gcsPromise = null;
    let gcs = null;
    const ensureSolver = () => {
      if (!gcsPromise) {
        gcsPromise = root.Planegcs.createSolver().then((made) => (gcs = made));
      }
      return gcsPromise;
    };

    let plane = null;
    let editing = null; // the feature being redrawn, or null for a new sketch
    let sketch = model.emptySketch();
    let states = {};
    let dof = null;
    let tool = "select";
    let pending = []; // points placed for the shape in progress: {uv, reference}
    let hover = null; // {uv, hit}
    let selection = []; // entity ids and point references
    let press = null; // a pointer that went down in the select tool: {x, y, hit, uv, dragging}
    let dimension = null; // the open dimension box: {constraint, anchor, index}
    let message = "";
    let offsetPreview = null; // polylines of the offset the open box would make
    let active = false;
    let dirty = false;
    let selectedConstraint = null; // id of the glyph or dimension label that is selected
    let conflicting = []; // ids of the constraints the last refused change ran into
    let showConstraints = true;
    let undoStack = [];
    let redoStack = [];
    // Only for a sketch already in the document: what the document holds as of
    // the last sync, what the sketch was when it opened, and the queue of syncs.
    let liveSync = false;
    let synced = null;
    let original = null;
    let queue = Promise.resolve();

    // --- Plane geometry ----------------------------------------------------

    const toWorld = ([u, v]) =>
      plane.origin.clone().addScaledVector(plane.xDir, u).addScaledVector(plane.yDir, v);

    function pointerUV(event) {
      const ray = host.rayFor(event);
      const target = new THREE.Vector3();
      const infinite = new THREE.Plane().setFromNormalAndCoplanarPoint(plane.normal, plane.origin);
      if (!ray.intersectPlane(infinite, target)) {
        return null;
      }
      const local = target.sub(plane.origin);
      return [local.dot(plane.xDir), local.dot(plane.yDir)];
    }

    /// A pick radius of `pixels` on screen, in mm on the plane.
    const radius = (pixels, uv) => host.worldRadius(pixels, toWorld(uv || [0, 0]));

    function polyline(entity) {
      if (entity.kind === "line") {
        return [entity.p0, entity.p1];
      }
      const steps = entity.kind === "circle" ? 72 : 48;
      const [start, end] = entity.kind === "circle" ? [0, TWO_PI] : [entity.a0, entity.a1];
      return Array.from({ length: steps + 1 }, (_, step) => {
        const angle = start + ((end - start) * step) / steps;
        return [entity.c[0] + entity.r * Math.cos(angle), entity.c[1] + entity.r * Math.sin(angle)];
      });
    }

    // --- Drawing the sketch --------------------------------------------------

    function dispose(object) {
      object.geometry.dispose();
      object.material.dispose();
    }

    function line(points, colour, dashed) {
      const geometry = new THREE.BufferGeometry().setFromPoints(points.map(toWorld));
      const material = dashed
        ? new THREE.LineDashedMaterial({
            color: host.srgb(colour),
            depthTest: false,
            dashSize: radius(7),
            gapSize: radius(4),
          })
        : new THREE.LineBasicMaterial({ color: host.srgb(colour), depthTest: false });
      const object = new THREE.Line(geometry, material);
      if (dashed) {
        object.computeLineDistances();
      }
      object.renderOrder = 1000;
      return object;
    }

    /// Straight pieces in world space, as the pairs of points they run between.
    function segments(points, colour) {
      const object = new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: host.srgb(colour), depthTest: false }),
      );
      object.renderOrder = 1002;
      return object;
    }

    const isReferenceEntity = (id) =>
      sketch.entities.some((entity) => entity.id === id && entity.reference);

    const chosen = () => {
      const constraint =
        selectedConstraint && sketch.constraints.find((item) => item.id === selectedConstraint);
      return constraint ? model.constraintEntities(sketch, constraint) : [];
    };

    function colourOf(id) {
      if (selection.includes(id) || chosen().includes(id)) {
        return COLOURS.selected;
      }
      if (
        hover?.hit &&
        hover.hit.kind === "entity" &&
        hover.hit.reference === id &&
        tool !== "line"
      ) {
        return COLOURS.hover;
      }
      if (isReferenceEntity(id)) {
        return COLOURS.reference;
      }
      return states[id] === "fixed" ? COLOURS.fixed : COLOURS.free;
    }

    let dots = null;
    function render() {
      while (group.children.length > 0) {
        const child = group.children.pop();
        dispose(child);
      }
      // Construction shapes and projected references are dashed: they bound no region.
      for (const entity of sketch.entities) {
        group.add(
          line(polyline(entity), colourOf(entity.id), entity.construction || entity.reference),
        );
      }

      const references = [
        model.ORIGIN,
        ...sketch.entities.flatMap((entity) =>
          solver.POINTS[entity.kind].map((name) => `${entity.id}.${name}`),
        ),
      ];
      const dotColours = references.map((reference) => {
        const isHover =
          hover?.hit && hover.hit.kind === "point" && hover.hit.reference === reference;
        const plain = reference === model.ORIGIN ? COLOURS.origin : 0xff_ff_ff;
        return new THREE.Color(
          host.srgb(
            selection.includes(reference) ? COLOURS.selected : isHover ? COLOURS.hover : plain,
          ),
        );
      });
      const geometry = new THREE.BufferGeometry().setFromPoints(
        references.map((reference) => toWorld(solver.pointOf(sketch, reference))),
      );
      geometry.setAttribute(
        "color",
        new THREE.Float32BufferAttribute(
          dotColours.flatMap((colour) => [colour.r, colour.g, colour.b]),
          3,
        ),
      );
      dots = new THREE.Points(
        geometry,
        new THREE.PointsMaterial({
          size: 7,
          sizeAttenuation: false,
          vertexColors: true,
          depthTest: false,
        }),
      );
      dots.renderOrder = 1001;
      group.add(dots);

      const preview = previewShape();
      if (preview) {
        group.add(line(preview, COLOURS.preview));
      }
      if (offsetPreview) {
        offsetPreview.forEach((shape) => group.add(line(shape, COLOURS.preview)));
      }
      if (hover?.project) {
        group.add(segments(hover.project.segments, COLOURS.hover));
      }
      renderLabels();
      renderSnap();
      renderPanel();
    }

    // --- The snap marker -----------------------------------------------------------

    const snapMarker = document.createElement("div");
    snapMarker.className = "sketch-snap";
    snapMarker.style.display = "none";
    snapMarker.innerHTML = '<span class="glyph"></span><span class="text"></span>';
    ui.labels.appendChild(snapMarker);

    const SNAP_NAMES = { origin: "Origin", p0: "Endpoint", p1: "Endpoint", c: "Centre" };

    /// What the next click would snap to, in words: "Centre", "On circle".
    const snapName = (snapped) =>
      snapped.reference ? SNAP_NAMES[snapped.reference.split(".").pop()] : snapped.label;

    function renderSnap() {
      const snapped = active && hover?.snap;
      snapMarker.style.display = snapped ? "block" : "none";
      if (!snapped) {
        return;
      }
      snapMarker.dataset.kind = snapped.reference ? "point" : "curve";
      snapMarker.lastChild.textContent = snapName(snapped);
    }

    /// The shape the next click would finish, following the pointer.
    function previewShape() {
      if (!hover || pending.length === 0) {
        return null;
      }
      const from = pending[0].uv;
      const to = hover.uv;
      if (tool === "line") {
        return [from, to];
      }
      if (tool === "rectangle") {
        return [from, [to[0], from[1]], to, [from[0], to[1]], from];
      }
      if (tool === "circle") {
        const r = Math.hypot(to[0] - from[0], to[1] - from[1]);
        return polyline({ kind: "circle", c: from, r });
      }
      if (tool === "arc") {
        if (pending.length === 1) {
          return [from, to];
        }
        const arc = model.arcThrough(from, pending[1].uv, to);
        return arc ? polyline({ kind: "arc", ...arc }) : [from, pending[1].uv];
      }
      return null;
    }

    // --- Dimension labels -----------------------------------------------------

    let labels = [];
    const VALUE_KINDS = ["distance", "distance_x", "distance_y", "radius", "diameter", "angle"];
    const FORMAT = {
      distance: (value) => value.toFixed(2),
      distance_x: (value) => `↔ ${value.toFixed(2)}`,
      distance_y: (value) => `↕ ${value.toFixed(2)}`,
      radius: (value) => `R${value.toFixed(2)}`,
      diameter: (value) => `⌀${value.toFixed(2)}`,
      angle: (value) => `${value.toFixed(1)}°`,
    };

    // Labels are kept between renders and matched by constraint id and position.
    // Rebuilding them on every render would replace the element under the mouse
    // between the two clicks of a double-click, and the second click would miss.
    const elements = new Map();

    function labelFor(key, text, className, constraint, index) {
      let element = elements.get(key);
      if (!element) {
        element = document.createElement("div");
        element.addEventListener("click", (event) => {
          event.stopPropagation();
          selectConstraint(element.dataset.constraintId);
        });
        ui.labels.appendChild(element);
        elements.set(key, element);
      }
      element.className =
        className +
        (constraint.id === selectedConstraint ? " selected" : "") +
        (conflicting.includes(constraint.id) ? " conflict" : "");
      element.textContent = text;
      element.dataset.constraint = String(index);
      element.dataset.constraintId = constraint.id;
      return element;
    }

    function renderLabels() {
      const wanted = new Set();
      labels = [];
      const stacked = new Map();
      sketch.constraints.forEach((constraint, index) => {
        if (VALUE_KINDS.includes(constraint.kind)) {
          const anchor = model.dimensionAnchor(sketch, constraint);
          if (!anchor) {
            return;
          }
          const key = `${constraint.id}:dimension`;
          wanted.add(key);
          const element = labelFor(
            key,
            FORMAT[constraint.kind](constraint.value),
            "sketch-dimension-label",
            constraint,
            index,
          );
          element.title = `${describe(constraint)}. Double-click to change, Delete removes it`;
          element.ondblclick = (event) => {
            event.stopPropagation();
            openDimension({ constraint, anchor, index });
          };
          labels.push({ element, anchor, index, dx: 6, dy: -22 });
          return;
        }
        if (!showConstraints) {
          return;
        }
        model.glyphAnchors(sketch, constraint).forEach((anchor, position) => {
          // Glyphs that land on one spot sit side by side, not on top of each other.
          const spot = anchor.map((value) => value.toFixed(2)).join(",");
          const beside = stacked.get(spot) || 0;
          stacked.set(spot, beside + 1);
          const key = `${constraint.id}:${position}`;
          wanted.add(key);
          const element = labelFor(key, GLYPHS[constraint.kind], "sketch-glyph", constraint, index);
          element.title = `${describe(constraint)}. Click to select, Delete removes it`;
          labels.push({ element, anchor, index, dx: 4 + beside * 18, dy: 4 });
        });
      });
      for (const [key, element] of elements) {
        if (wanted.has(key)) {
          continue;
        }
        element.remove();
        elements.delete(key);
      }
    }

    const describe = (constraint) => model.describeConstraint(constraint);

    function selectConstraint(id) {
      selectedConstraint = id;
      selection = [];
      conflicting = [];
      setMessage(
        describe(sketch.constraints.find((constraint) => constraint.id === id)) +
          " is selected. Delete removes it.",
      );
      render();
    }

    /// Puts each label, and the open box, where its anchor is on screen. Runs
    /// every frame, because the camera can move under them.
    function frame() {
      if (!active) {
        return;
      }
      const rect = renderer.domElement.getBoundingClientRect();
      const place = (element, anchor, dx, dy) => {
        const projected = toWorld(anchor).project(camera);
        element.style.left = `${rect.left + ((projected.x + 1) / 2) * rect.width + dx}px`;
        element.style.top = `${rect.top + ((1 - projected.y) / 2) * rect.height + dy}px`;
      };
      labels.forEach((label) => place(label.element, label.anchor, label.dx, label.dy));
      if (dimension) {
        place(ui.dimension, dimension.anchor, 6, 6);
      }
      if (hover?.snap) {
        place(snapMarker, hover.snap.uv, 0, 0);
      }
    }

    function openDimension(open) {
      dimension = open;
      ui.dimension.style.display = "block";
      ui.dimension.value = open.constraint.value.toFixed(open.constraint.kind === "angle" ? 1 : 2);
      ui.dimension.focus();
      ui.dimension.select();
      const orient =
        open.selection &&
        open.selection.length === 2 &&
        open.selection.every(model.isPointReference)
          ? ". H across, V up, A straight"
          : "";
      setMessage(`${open.constraint.kind}: type a value and press Enter${orient}`);
    }

    function closeDimension() {
      dimension = null;
      offsetPreview = null;
      selection = [];
      ui.dimension.style.display = "none";
      ui.dimension.blur();
      render();
    }

    function acceptDimension() {
      const value = Number.parseFloat(ui.dimension.value);
      if (!Number.isFinite(value) || (value <= 0 && dimension.constraint.kind !== "angle")) {
        setMessage("Type a number above zero");
        return;
      }
      if (dimension.offset) {
        try {
          const made = root.SketchTools.offsetChain(
            sketch,
            dimension.offset.id,
            dimension.offset.side * value,
          );
          if (change(made.sketch)) {
            closeDimension();
            selection = made.ids;
            setMessage(
              "Offset " +
                made.ids.length +
                (made.ids.length === 1 ? " shape" : " shapes") +
                " by " +
                value,
            );
            render();
          }
        } catch (error) {
          setMessage(error.message);
        }
        return;
      }
      if (dimension.fillet) {
        try {
          const rounded = model.addFillet(sketch, dimension.fillet, value);
          if (change(rounded.sketch)) {
            closeDimension();
          }
        } catch (error) {
          setMessage(error.message);
        }
        return;
      }
      const constraint = { ...dimension.constraint, value };
      const next =
        dimension.index === null || dimension.index === undefined
          ? model.withConstraint(sketch, constraint)
          : model.replaceConstraint(sketch, dimension.index, constraint);
      if (change(next)) {
        closeDimension();
      }
    }

    // --- Changing the sketch ---------------------------------------------------

    function setMessage(text) {
      message = text;
      renderPanel();
    }

    /// Solves `next` and adopts it, or refuses and says why. Every edit goes
    /// through here, so a constraint that contradicts the others is turned
    /// away with the sketch untouched.
    function change(next, options) {
      if (!gcs) {
        return false;
      }
      const solved = solver.solve(gcs, next, options);
      if (!solved.ok) {
        // The solver reports every constraint in the clashing group, which can
        // be most of a rectangle. The dimensions are what a person set on
        // purpose, so name those, and fall back to the group when there are none.
        const named = solved.conflicting.map((index) => next.constraints[index]).filter(Boolean);
        const dimensions = named.filter((constraint) => VALUE_KINDS.includes(constraint.kind));
        const shown = (dimensions.length > 0 ? dimensions : named).slice(0, 3);
        conflicting = shown.map((constraint) => constraint.id);
        setMessage(
          shown.length > 0
            ? `That conflicts with ${shown.map(describe).join(" and ")}`
            : "That conflicts with the constraints already there",
        );
        render();
        return false;
      }
      // A drag is one step in the history, taken when it starts.
      if (!options?.drag) {
        remember();
      }
      sketch = solved.sketch;
      dirty = true;
      conflicting = [];
      refreshStates(options?.drag);
      message = "";
      render();
      if (!options?.drag) {
        sync();
      }
      return true;
    }

    // --- Undo and redo ---------------------------------------------------------
    // Local to the sketcher, so Cmd+Z in a sketch steps back through this
    // sketch's own changes and never through the document's.

    function remember() {
      undoStack.push(copy(sketch));
      undoStack.splice(0, undoStack.length - UNDO_LIMIT);
      redoStack = [];
    }

    function step(from, to, word) {
      if (from.length === 0) {
        setMessage(`Nothing to ${word}`);
        return;
      }
      to.push(copy(sketch));
      sketch = from.pop();
      selection = [];
      selectedConstraint = null;
      conflicting = [];
      dirty = true;
      if (gcs) {
        refreshStates(false);
      }
      setMessage(word === "undo" ? "Undone" : "Redone");
      render();
      sync();
    }

    const undo = () => step(undoStack, redoStack, "undo");
    const redo = () => step(redoStack, undoStack, "redo");

    // --- Keeping a sketch in the document in step -------------------------------

    /// Sends what changed since the last sync. One at a time, in order, so a
    /// slow reply cannot be overtaken by the change after it.
    function sync() {
      if (!liveSync) {
        return queue;
      }
      queue = queue.then(pushChanges);
      return queue;
    }

    async function pushChanges() {
      while (active && liveSync && !model.sameSketch(sketch, synced)) {
        const sent = copy(sketch);
        try {
          await host.updateSketch(editing.id, sent);
          synced = sent;
        } catch (error) {
          if (!(await recover(error))) {
            return;
          }
        }
      }
    }

    /// The engine refused an update. A stale revision means something else
    /// edited the document: if that left this sketch alone, send again, and if
    /// not, take theirs and drop the change that lost.
    async function recover(error) {
      if (!STALE.test(error.message)) {
        setMessage(`Could not save: ${error.message}`);
        return false;
      }
      const remote = await host.fetchSketch(editing.id);
      if (!remote) {
        setMessage("This sketch was removed from the document");
        liveSync = false;
        return false;
      }
      if (model.sameSketch(model.withConstraintIds(remote), model.withConstraintIds(synced))) {
        return true;
      }
      adopt(remote);
      setMessage("It was changed elsewhere, so your last change was dropped");
      return true;
    }

    /// Replaces the sketch with the document's, when someone else changed it.
    function adopt(remote) {
      sketch = model.withConstraintIds(remote);
      synced = copy(remote);
      original = copy(sketch);
      undoStack = [];
      redoStack = [];
      selection = [];
      selectedConstraint = null;
      conflicting = [];
      if (gcs) {
        refreshStates(false);
      }
      render();
    }

    /// The viewer calls this when the document changes. A change that is not
    /// this sketcher's own is adopted. One it made itself matches `synced`.
    async function remoteChanged() {
      if (!active || !liveSync) {
        return;
      }
      await queue;
      const remote = await host.fetchSketch(editing.id);
      if (!active || !remote || !model.sameSketch(sketch, synced)) {
        return;
      }
      if (model.sameSketch(model.withConstraintIds(remote), model.withConstraintIds(synced))) {
        return;
      }
      adopt(remote);
      setMessage("Updated from outside");
    }

    /// Which entities are fully constrained, for the colouring. Skipped while
    /// dragging: it costs a solve per point and the colours settle on release.
    function refreshStates(dragging) {
      if (dragging) {
        return;
      }
      const result = solver.entityStates(gcs, sketch);
      states = result.states;
      dof = result.dof;
    }

    // --- Tools -------------------------------------------------------------------

    function setTool(name) {
      if (!TOOL_KEYS_INVERSE.includes(name)) {
        return;
      }
      tool = name;
      pending = [];
      selection = [];
      hover = null;
      if (dimension) {
        closeDimension();
      }
      setMessage("");
      render();
    }
    const TOOL_KEYS_INVERSE = Object.values(TOOL_KEYS);

    /// The world axes that are shown and lie in the sketch plane, as the sketch
    /// axes a point can be put on. An axis that crosses the plane, or runs
    /// beside it, gives nothing to snap to, so Z on an XY sketch adds nothing
    /// that the origin does not already give.
    function shownAxes() {
      const along = {
        axis_u: plane.xDir.clone().normalize(),
        axis_v: plane.yDir.clone().normalize(),
      };
      return host.visibleAxes().flatMap((world) => {
        const offAxis = plane.origin
          .clone()
          .addScaledVector(world.direction, -plane.origin.dot(world.direction));
        if (offAxis.length() > 1e-6) {
          return [];
        }
        return Object.entries(along)
          .filter(([, direction]) => Math.abs(direction.dot(world.direction)) > 1 - 1e-6)
          .map(([ref]) => ({ ref, name: `${world.name} axis` }));
      });
    }

    /// Where a click means: a point if one is near, else a spot on a curve or
    /// a shown axis, else the raw position.
    const target = (uv) => model.snap(sketch, uv, radius(SNAP_PIXELS, uv), shownAxes());

    function place(event) {
      const raw = pointerUV(event);
      if (!raw) {
        return;
      }
      let point = target(raw);
      if (tool === "line" && pending.length > 0 && !point.reference && !point.curve) {
        // Drawing nearly level or upright makes the line exactly so.
        const direction = model.alignment(pending[0].uv, point.uv, ALIGN_DEGREES);
        if (direction === "horizontal") {
          point = {
            uv: [point.uv[0], pending[0].uv[1]],
            reference: null,
            curve: null,
            align: direction,
          };
        }
        if (direction === "vertical") {
          point = {
            uv: [pending[0].uv[0], point.uv[1]],
            reference: null,
            curve: null,
            align: direction,
          };
        }
      }
      pending.push(point);
      if (pending.length < 1 + PLACING[tool].length) {
        setMessage(PLACING[tool][pending.length - 1]);
        render();
        return;
      }
      finishShape();
    }

    /// The arc's two ends joined to whichever picked point is nearer: an arc may
    /// run the other way round from the order the points were picked in.
    function arcJoins(next, arcId, points) {
      const ends = ["p0", "p1"].map((name) => solver.pointOf(next, `${arcId}.${name}`));
      return points.slice(0, 2).map((point) => {
        const nearer =
          Math.hypot(ends[0][0] - point.uv[0], ends[0][1] - point.uv[1]) <=
          Math.hypot(ends[1][0] - point.uv[0], ends[1][1] - point.uv[1])
            ? "p0"
            : "p1";
        return [point, `${arcId}.${nearer}`];
      });
    }

    /// The sketch with the current tool's shape added from `points`, and the
    /// (picked point, new point) pairs to join where a pick snapped. Null when
    /// the shape has no size.
    function drawnShape(points) {
      if (tool === "line") {
        const added = model.addLine(sketch, points[0].uv, points[1].uv);
        const next = points[1].align
          ? model.withConstraint(added.sketch, { kind: points[1].align, e: added.id })
          : added.sketch;
        return {
          added,
          next,
          joins: [
            [points[0], `${added.id}.p0`],
            [points[1], `${added.id}.p1`],
          ],
        };
      }
      if (tool === "rectangle") {
        const added = model.addRectangle(sketch, points[0].uv, points[1].uv);
        return added?.sketch
          ? {
              added,
              next: added.sketch,
              joins: [
                [points[0], `${added.ids[0]}.p0`],
                [points[1], `${added.ids[2]}.p0`],
              ],
            }
          : null;
      }
      if (tool === "circle") {
        const r = Math.hypot(points[1].uv[0] - points[0].uv[0], points[1].uv[1] - points[0].uv[1]);
        const added = r > 1e-6 ? model.addCircle(sketch, points[0].uv, r) : null;
        return added?.sketch
          ? { added, next: added.sketch, joins: [[points[0], `${added.id}.c`]] }
          : null;
      }
      if (tool === "arc") {
        const added = model.addArc(sketch, points[0].uv, points[1].uv, points[2].uv);
        return added?.sketch
          ? { added, next: added.sketch, joins: arcJoins(added.sketch, added.id, points) }
          : null;
      }
      return null;
    }

    function finishShape() {
      const points = pending;
      const drawn = drawnShape(points);
      const added = drawn?.added || null;
      let next = drawn?.next || null;
      const joins = drawn?.joins || [];
      if (!next) {
        pending = [];
        setMessage("That shape has no size");
        render();
        return;
      }
      for (const [snapped, fresh] of joins) {
        next = model.join(next, snapped, fresh);
      }
      const closedLoop =
        tool === "line" && points[1].reference && points[1].reference === chainStart;
      if (!change(next)) {
        pending = [];
        render();
        return;
      }
      if (tool === "line" && !closedLoop) {
        if (!chainStart) {
          chainStart = points[0].reference || `${added.id}.p0`;
        }
        pending = [{ uv: solver.pointOf(sketch, `${added.id}.p1`), reference: `${added.id}.p1` }];
        setMessage(PLACING.line[0]);
      } else {
        chainStart = null;
        pending = [];
      }
      render();
    }
    let chainStart = null;

    function endChain() {
      pending = [];
      chainStart = null;
      setMessage("");
      render();
    }

    // --- Selecting and dragging -----------------------------------------------------

    function toggleSelected(reference, additive) {
      if (selection.includes(reference)) {
        selection = selection.filter((item) => item !== reference);
      } else {
        selection = additive ? [...selection, reference] : [reference];
      }
    }

    function dimensionClick(hit) {
      if (!hit) {
        return;
      }
      toggleSelected(hit.reference, true);
      // A line or circle alone is enough for a dimension. Points and lines
      // want a second pick, and a second line turns the length into an angle.
      const suggestion = model.suggestDimension(sketch, selection);
      if (suggestion) {
        openDimension({
          constraint: suggestion.constraint,
          anchor: suggestion.anchor,
          index: null,
          selection: [...selection],
        });
      } else {
        render();
      }
    }

    /// A new dimension's box is open on one pick, say a line's length. Clicking
    /// another shape or point then measures between the two instead: two lines
    /// give the gap between them, two points the distance, a line and a circle nothing.
    function addSecondPick(event) {
      const isNew = dimension.index === null || dimension.index === undefined;
      if (tool !== "dimension" || !isNew || !dimension.selection) {
        return;
      }
      const under = selectionUnder(event);
      if (!under?.hit || dimension.selection.includes(under.hit.reference)) {
        return;
      }
      const picks = [...dimension.selection, under.hit.reference];
      const suggestion = model.suggestDimension(sketch, picks);
      if (!suggestion) {
        setMessage("Those two cannot be dimensioned together");
        return;
      }
      selection = picks;
      openDimension({
        constraint: suggestion.constraint,
        anchor: suggestion.anchor,
        index: null,
        selection: picks,
      });
      render();
    }

    function selectionUnder(event) {
      const uv = pointerUV(event);
      return uv ? { uv, hit: model.hit(sketch, uv, radius(SNAP_PIXELS, uv)) } : null;
    }

    function applyConstraint(kind) {
      try {
        const constraint = model.constraintFor(sketch, kind, selection);
        if (change(model.withConstraint(sketch, constraint))) {
          selection = [];
        }
      } catch (error) {
        setMessage(error.message);
      }
      render();
    }

    function deleteSelection() {
      if (selectedConstraint) {
        const id = selectedConstraint;
        selectedConstraint = null;
        change(model.removeConstraintById(sketch, id));
        return;
      }
      const known = new Set(sketch.entities.map((entity) => entity.id));
      const ids = [...new Set(selection.map((reference) => model.entityIdOf(reference)))].filter(
        (id) => known.has(id),
      );
      if (ids.length === 0) {
        return;
      }
      selection = [];
      change(model.deleteEntities(sketch, ids));
    }

    /// The shapes and the line for a mirror: the selection's last pick, if it is
    /// a line, is what to mirror across, and the rest are mirrored. Null when the
    /// selection is not that.
    function mirrorPlan() {
      const ids = [...new Set(selection.map((reference) => model.entityIdOf(reference)))].filter(
        (id) => sketch.entities.some((entity) => entity.id === id),
      );
      const last = selection.length > 0 ? model.entityIdOf(selection.at(-1)) : null;
      const line = sketch.entities.find((entity) => entity.id === last && entity.kind === "line");
      return line && ids.length > 1 ? { ids: ids.filter((id) => id !== last), line: last } : null;
    }

    /// Copies the selected shapes across the selected line, tied to the originals
    /// by symmetric constraints, so the copy follows when the original moves.
    function mirrorSelection() {
      const plan = mirrorPlan();
      if (!plan) {
        setMessage("Select the shapes, then shift-click the line to mirror across, then Mirror");
        return;
      }
      try {
        const mirrored = model.mirrorEntities(sketch, plan.ids, plan.line);
        if (change(mirrored.sketch)) {
          selection = mirrored.ids;
          setMessage(
            "Mirrored " +
              mirrored.ids.length +
              (mirrored.ids.length === 1 ? " shape" : " shapes") +
              ", tied to the originals",
          );
        }
      } catch (error) {
        setMessage(error.message);
      }
      render();
    }

    /// Flips the selected shapes between solid and construction. Construction
    /// shapes are dashed and bound no region, so they guide without being extruded.
    function toggleConstruction() {
      const ids = [...new Set(selection.map((reference) => model.entityIdOf(reference)))];
      const next = model.toggleConstruction(sketch, ids);
      if (!next) {
        setMessage(
          ids.length > 0
            ? "A projected reference is always construction"
            : "Select a shape first, then toggle construction",
        );
        return;
      }
      change(next);
      setMessage(
        next.entities.some((entity) => ids.includes(entity.id) && entity.construction)
          ? "Now construction: dashed, no region"
          : "Now a solid shape again",
      );
    }

    /// Opens the radius box for rounding the corner at `reference`.
    function startFillet(reference) {
      const radius = model.suggestFilletRadius(sketch, reference);
      if (radius === null) {
        setMessage("Click a point where exactly two lines meet");
        return;
      }
      openDimension({
        constraint: { kind: "radius", value: radius },
        anchor: solver.pointOf(sketch, reference),
        index: null,
        fillet: reference,
      });
      setMessage("Fillet radius: type a value and press Enter");
    }

    /// A click with Trim, Extend or Offset. Trim and Extend edit at once; Offset
    /// opens the distance box and waits for the side and the number.
    function cutClick(under) {
      // The nearest curve, not what is under a point: the origin or a corner is no use here.
      const curve = under && model.snapToCurve(sketch, under.uv, radius(SNAP_PIXELS, under.uv));
      const id = curve?.entity;
      if (!id) {
        setMessage("Click a line, arc or circle");
        return;
      }
      try {
        if (tool === "offset") {
          const side = root.SketchTools.sideOf(sketch, id, under.uv) || 1;
          openDimension({
            constraint: { kind: "distance", value: 5 },
            anchor: under.uv,
            index: null,
            offset: { id, side, edited: false },
          });
          setMessage("Move to the side to offset to, type a distance, press Enter");
          updateOffsetPreview();
          return;
        }
        const edit =
          tool === "trim"
            ? root.SketchTools.trimEntity(sketch, id, under.uv)
            : root.SketchTools.extendEntity(sketch, id, under.uv);
        if (change(edit.sketch)) {
          selection = [];
          setMessage(tool === "trim" ? `Trimmed ${id}` : `Extended ${id}`);
        }
      } catch (error) {
        setMessage(error.message);
      }
    }

    /// The pointer moved with the offset box open: the side it is on, and the
    /// distance to the shape until a number has been typed.
    function moveOffset(uv) {
      const open = dimension.offset;
      const entity = sketch.entities.find((item) => item.id === open.id);
      const side = root.SketchTools.sideOf(sketch, open.id, uv);
      if (side) {
        open.side = side;
      }
      if (!open.edited) {
        ui.dimension.value = String(
          Math.max(0.1, Math.round(model.distanceToEntity(entity, uv) * 10) / 10),
        );
        ui.dimension.select(); // the next key replaces it
      }
      updateOffsetPreview();
    }

    function updateOffsetPreview() {
      const open = dimension?.offset;
      offsetPreview = null;
      const value = Number.parseFloat(ui.dimension.value);
      if (open && value > 0) {
        try {
          const made = root.SketchTools.offsetChain(sketch, open.id, open.side * value);
          offsetPreview = made.ids.map((id) =>
            polyline(made.sketch.entities.find((entity) => entity.id === id)),
          );
        } catch (error) {
          setMessage(error.message);
        }
      }
      render();
    }

    let projecting = false;

    /// Asks the engine to project what the pointer is over into this sketch,
    /// and adds it as a locked reference.
    async function projectPick(pick) {
      if (projecting) {
        return;
      }
      projecting = true;
      setMessage("Projecting...");
      try {
        const found = await host.projectReference(plane, pick.ref);
        const added = model.addReference(sketch, found.source, found.entity);
        if (!added) {
          setMessage("That edge is already projected here");
        } else if (change(added.sketch)) {
          selection = [added.id];
          setMessage(`Projected as ${added.id}, locked. Dimension it with D, or Delete removes it`);
        }
      } catch (error) {
        setMessage(`Cannot project that: ${error.message}`);
      } finally {
        projecting = false;
        render();
      }
    }

    // --- Pointer and key events, called by the viewer ----------------------------------

    /// A press that has moved far enough on something draggable becomes a drag.
    function startDragIfMoved(event) {
      if (
        press &&
        !press.dragging &&
        Math.hypot(event.clientX - press.x, event.clientY - press.y) > DRAG_PIXELS &&
        press.hit &&
        press.hit.reference !== model.ORIGIN &&
        !isReferenceEntity(model.entityIdOf(press.hit.reference))
      ) {
        press.dragging = true;
        remember();
        host.setCameraEnabled(false);
      }
    }

    /// What the pointer at `uv` is over and snaps to, for the current tool.
    function hoverAt(uv, event) {
      const under = ["line", "select", "dimension", "fillet", "trim", "extend", "offset"].includes(
        tool,
      )
        ? model.hit(sketch, uv, radius(SNAP_PIXELS, uv))
        : null;
      const snapped =
        tool === "line" || tool === "rectangle" || tool === "circle" || tool === "arc"
          ? target(uv)
          : { uv };
      const found = {
        uv: snapped.uv,
        hit: under,
        snap: snapped.reference || snapped.curve ? snapped : null,
      };
      if (tool === "line" && pending.length > 0 && !snapped.reference && !snapped.curve) {
        const direction = model.alignment(pending[0].uv, uv, ALIGN_DEGREES);
        if (direction === "horizontal") {
          found.uv = [uv[0], pending[0].uv[1]];
        }
        if (direction === "vertical") {
          found.uv = [pending[0].uv[0], uv[1]];
        }
      }
      if (tool === "project") {
        found.project = host.pickProjectable(event);
      }
      return found;
    }

    function cursorFor(over) {
      if (tool === "project") {
        return over.project ? "pointer" : "crosshair";
      }
      return over.hit && tool === "select" ? "move" : "crosshair";
    }

    function pointerMove(event) {
      const uv = pointerUV(event);
      if (!uv) {
        return;
      }
      startDragIfMoved(event);
      if (press?.dragging) {
        const delta = [uv[0] - press.uv[0], uv[1] - press.uv[1]];
        if (change(sketch, { drag: { ref: press.hit.reference, delta } })) {
          press.uv = uv;
        }
        return;
      }
      if (dimension?.offset) {
        moveOffset(uv);
        return;
      }
      hover = hoverAt(uv, event);
      renderer.domElement.style.cursor = cursorFor(hover);
      render();
    }

    function pointerDown(event) {
      if (event.button !== 0) {
        return;
      }
      if (dimension?.offset) {
        acceptDimension();
        return;
      }
      if (dimension) {
        addSecondPick(event);
        return;
      }
      if (tool === "trim" || tool === "extend" || tool === "offset") {
        cutClick(selectionUnder(event));
        return;
      }
      if (tool === "select") {
        const under = selectionUnder(event);
        if (!under) {
          return;
        }
        press = {
          x: event.clientX,
          y: event.clientY,
          hit: under.hit,
          uv: under.uv,
          dragging: false,
          additive: event.shiftKey,
        };
        return;
      }
      if (tool === "dimension") {
        const under = selectionUnder(event);
        dimensionClick(under?.hit);
        return;
      }
      if (tool === "fillet") {
        const under = selectionUnder(event);
        if (under?.hit && under.hit.kind === "point") {
          startFillet(under.hit.reference);
        } else {
          setMessage("Click a point where two lines meet");
        }
        return;
      }
      if (tool === "project") {
        const pick = host.pickProjectable(event);
        if (pick) {
          projectPick(pick);
        } else {
          setMessage("Nothing to project there. Click an edge of another sketch, or of the part");
        }
        return;
      }
      place(event);
    }

    function pointerUp() {
      if (!press) {
        return;
      }
      const finished = press;
      press = null;
      if (finished.dragging) {
        host.setCameraEnabled(true);
        refreshStates(false);
        // A drag the solver refused throughout is not a step to undo.
        if (undoStack.length > 0 && model.sameSketch(undoStack.at(-1), sketch)) {
          undoStack.pop();
        }
        render();
        sync();
        return;
      }
      selectedConstraint = null;
      if (finished.hit) {
        toggleSelected(finished.hit.reference, finished.additive);
      } else if (!finished.additive) {
        selection = [];
      }
      render();
    }

    function doubleClick() {
      if (tool === "line") {
        endChain();
      }
    }

    /// True when the sketcher used the key.
    // biome-ignore lint/complexity/noExcessiveCognitiveComplexity: one branch per key, in the order the keys take priority
    // biome-ignore lint/complexity/noExcessiveLinesPerFunction: one branch per key, in the order the keys take priority
    function keyDown(event) {
      if (event.target === ui.dimension) {
        const orientation = ORIENTATIONS[event.key.toLowerCase()];
        if (event.key === "Enter") {
          acceptDimension();
        } else if (event.key === "Escape") {
          closeDimension();
        } else if (
          orientation &&
          dimension?.selection &&
          (dimension.index === null || dimension.index === undefined)
        ) {
          const suggestion = model.suggestDimension(sketch, dimension.selection, orientation);
          if (suggestion) {
            openDimension({
              constraint: suggestion.constraint,
              anchor: suggestion.anchor,
              index: null,
              selection: dimension.selection,
            });
          }
        } else {
          return false;
        }
        event.preventDefault();
        return true;
      }
      const key = event.key.toLowerCase();
      if ((event.metaKey || event.ctrlKey) && (key === "z" || key === "y")) {
        if (key === "y" || event.shiftKey) {
          redo();
        } else {
          undo();
        }
        event.preventDefault();
        return true;
      }
      if (event.metaKey || event.ctrlKey) {
        return false;
      }
      if (event.key === "Escape") {
        if (pending.length > 0) {
          endChain();
        } else if (selection.length > 0 || selectedConstraint) {
          selection = [];
          selectedConstraint = null;
          render();
        } else if (tool !== "select") {
          setTool("select");
        } else if (sketch.entities.length === 0) {
          cancel();
        }
        return true;
      }
      if (event.key === "Enter") {
        if (tool === "line" && pending.length > 0) {
          endChain();
        } else {
          finish();
        }
        return true;
      }
      if (key === "x") {
        toggleConstruction();
        return true;
      }
      if (key === "m") {
        mirrorSelection();
        return true;
      }
      if (event.key === "Delete" || event.key === "Backspace") {
        deleteSelection();
        event.preventDefault();
        return true;
      }
      if (TOOL_KEYS[key]) {
        setTool(TOOL_KEYS[key]);
        return true;
      }
      return false;
    }

    // --- The panel -----------------------------------------------------------------------

    function renderPanel() {
      ui.tools.forEach((button) => button.classList.toggle("active", button.dataset.tool === tool));
      ui.constraints.forEach((button) => {
        let usable = true;
        try {
          model.constraintFor(sketch, button.dataset.constraint, selection);
        } catch {
          usable = false;
        }
        button.disabled = !usable;
      });
      if (ui.toggle) {
        ui.toggle.classList.toggle("active", showConstraints);
      }
      if (ui.mirror) {
        ui.mirror.disabled = !mirrorPlan();
      }
      if (ui.construction) {
        const known = new Set(
          sketch.entities.filter((entity) => !entity.reference).map((entity) => entity.id),
        );
        ui.construction.disabled = !selection.some((reference) =>
          known.has(model.entityIdOf(reference)),
        );
        ui.construction.classList.toggle(
          "active",
          selection.some((reference) =>
            sketch.entities.some(
              (entity) =>
                entity.id === model.entityIdOf(reference) &&
                entity.construction &&
                !entity.reference,
            ),
          ),
        );
      }
      const constrained =
        dof === 0 && sketch.entities.length > 0
          ? "Fully constrained"
          : dof === null
            ? ""
            : `${dof} freedom left`;
      ui.status.textContent = message || (pending.length > 0 ? "" : HINTS[tool]);
      ui.state.textContent = constrained;
    }

    // --- Starting and stopping ---------------------------------------------------------------

    /// Opens the sketcher on `newPlane` ({origin, xDir, yDir, normal} as three.js
    /// vectors). Pass `feature` to redraw an existing entity sketch.
    function begin(newPlane, feature) {
      plane = newPlane;
      editing = feature || null;
      const stored = feature
        ? { entities: copy(feature.entities), constraints: copy(feature.constraints || []) }
        : null;
      sketch = stored ? model.withConstraintIds(stored) : model.emptySketch();
      liveSync = Boolean(stored);
      synced = stored;
      original = copy(sketch);
      queue = Promise.resolve();
      undoStack = [];
      redoStack = [];
      selectedConstraint = null;
      conflicting = [];
      states = {};
      dof = null;
      tool = "select";
      pending = [];
      chainStart = null;
      selection = [];
      hover = null;
      press = null;
      dimension = null;
      dirty = false;
      active = true;
      ui.dimension.style.display = "none";
      ensureSolver().then(() => {
        if (!active) {
          return;
        }
        if (sketch.entities.length > 0) {
          refreshStates(false);
        }
        // A new sketch starts on the tool people reach for first.
        setTool(editing ? "select" : "line");
      });
      render();
    }

    function end() {
      active = false;
      liveSync = false;
      plane = null;
      editing = null;
      pending = [];
      selection = [];
      dimension = null;
      press = null;
      ui.dimension.style.display = "none";
      elements.forEach((element) => element.remove());
      elements.clear();
      labels = [];
      hover = null;
      snapMarker.style.display = "none";
      while (group.children.length > 0) {
        dispose(group.children.pop());
      }
    }

    async function finish() {
      if (sketch.entities.length === 0) {
        cancel();
        return;
      }
      if (!liveSync) {
        host.commit({
          type: "add_sketch",
          plane: host.planePayload(plane),
          entities: sketch.entities,
          constraints: sketch.constraints,
        });
        return;
      }
      await sync();
      // A sync that failed leaves the document behind the sketch. Send the whole sketch instead.
      if (!model.sameSketch(sketch, synced)) {
        host.commit({
          type: "update_sketch_entities",
          id: editing.id,
          entities: sketch.entities,
          constraints: sketch.constraints,
        });
        return;
      }
      host.close();
    }

    /// Throws the sketch away. One that is in the document has been saved as
    /// it was drawn, so this puts back what it was when the sketcher opened.
    async function cancel() {
      if (liveSync && original && !model.sameSketch(sketch, original)) {
        sketch = copy(original);
        await sync();
      }
      host.cancel();
    }

    function snapshot() {
      return {
        active,
        tool,
        entities: JSON.parse(JSON.stringify(sketch.entities)),
        constraints: JSON.parse(JSON.stringify(sketch.constraints)),
        dof,
        states: { ...states },
        selection: [...selection],
        snap: hover?.snap
          ? {
              name: snapName(hover.snap),
              reference: hover.snap.reference,
              curve: hover.snap.curve,
              uv: hover.snap.uv,
            }
          : null,
        message,
        dirty,
        editing: editing ? editing.id : null,
        constraintIds: sketch.constraints.map((constraint) => constraint.id),
        selectedConstraint,
        conflicting: [...conflicting],
        showConstraints,
        glyphs: labels.filter((label) => label.element.classList.contains("sketch-glyph")).length,
        undoDepth: undoStack.length,
        redoDepth: redoStack.length,
        synced: liveSync ? model.sameSketch(sketch, synced) : null,
      };
    }

    /// Where [u, v] is on screen, in page pixels, for tests to click.
    function screenPointOfUV(uv) {
      camera.updateMatrixWorld();
      const projected = toWorld(uv).project(camera);
      const rect = renderer.domElement.getBoundingClientRect();
      return {
        x: rect.left + ((projected.x + 1) / 2) * rect.width,
        y: rect.top + ((1 - projected.y) / 2) * rect.height,
      };
    }

    ui.tools.forEach((button) =>
      button.addEventListener("click", () => setTool(button.dataset.tool)),
    );
    ui.dimension.addEventListener("input", () => {
      if (!dimension?.offset) {
        return;
      }
      dimension.offset.edited = true;
      updateOffsetPreview();
    });
    ui.constraints.forEach((button) =>
      button.addEventListener("click", () => applyConstraint(button.dataset.constraint)),
    );
    ui.finish.addEventListener("click", finish);
    ui.cancel.addEventListener("click", cancel);
    if (ui.construction) {
      ui.construction.addEventListener("click", toggleConstruction);
    }
    if (ui.mirror) {
      ui.mirror.addEventListener("click", mirrorSelection);
    }
    if (ui.toggle) {
      ui.toggle.addEventListener("click", () => {
        showConstraints = !showConstraints;
        render();
      });
    }

    return {
      begin,
      end,
      frame,
      pointerMove,
      pointerDown,
      pointerUp,
      doubleClick,
      keyDown,
      remoteChanged,
      cancel,
      sync,
      snapshot,
      screenPointOfUV,
      get active() {
        return active;
      },
      CONSTRAINTS,
    };
  }

  root.createSketcher = createSketcher;
})(globalThis);
