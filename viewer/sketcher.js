// The sketch editor: drawing tools, dimensions, constraints and dragging, on a
// plane in the 3D view.
//
// The rules live elsewhere. sketch-model.js decides what a click adds and
// what a selection can be constrained to, and sketch-solver.js moves the
// geometry to obey the constraints. This file turns pointer and key events
// into calls to those two, and draws the result. It reaches the rest of the
// viewer only through the `host` object it is created with.
//
// Nothing here writes to the document until Finish. The sketch lives in
// memory while it is drawn, and one edit goes to the engine at the end.
(function (root) {
  const TWO_PI = 2 * Math.PI;
  const SNAP_PIXELS = 10;
  const ALIGN_DEGREES = 4;
  const DRAG_PIXELS = 4;
  const COLOURS = { free: 0x4da3ff, fixed: 0xe8e8e8, selected: 0xffaa00, hover: 0xffdd55, preview: 0x5be0a0 };
  const TOOL_KEYS = { s: "select", l: "line", r: "rectangle", c: "circle", a: "arc", d: "dimension" };
  const HINTS = {
    select: "Click to select, drag to move. Delete removes.",
    line: "Click to start a line, click again to place its end. Esc ends the chain.",
    rectangle: "Click a corner, then the opposite one",
    circle: "Click the centre, then a point on the circle",
    arc: "Click the start, the end, then a point the arc passes through",
    dimension: "Click a line, a circle, two points or two lines",
  };
  const PLACING = {
    line: ["Click the line's end"],
    rectangle: ["Click the opposite corner"],
    circle: ["Click a point on the circle"],
    arc: ["Click the arc's end", "Click a point the arc passes through"],
  };
  const CONSTRAINTS = ["coincident", "horizontal", "vertical", "parallel", "perpendicular", "tangent", "equal", "fix"];

  function createSketcher(host) {
    const { THREE, scene, camera, renderer, model, solver } = host;
    const group = new THREE.Group();
    scene.add(group);
    const ui = host.ui;

    let gcsPromise = null;
    let gcs = null;
    const ensureSolver = () => {
      if (!gcsPromise) gcsPromise = root.Planegcs.createSolver().then((made) => (gcs = made));
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
    let active = false;
    let dirty = false;

    // --- Plane geometry ----------------------------------------------------

    const toWorld = ([u, v]) => plane.origin.clone().addScaledVector(plane.xDir, u).addScaledVector(plane.yDir, v);

    function pointerUV(event) {
      const ray = host.rayFor(event);
      const target = new THREE.Vector3();
      const infinite = new THREE.Plane().setFromNormalAndCoplanarPoint(plane.normal, plane.origin);
      if (!ray.intersectPlane(infinite, target)) return null;
      const local = target.sub(plane.origin);
      return [local.dot(plane.xDir), local.dot(plane.yDir)];
    }

    /// A pick radius of `pixels` on screen, in mm on the plane.
    const radius = (pixels, uv) => host.worldRadius(pixels, toWorld(uv || [0, 0]));

    function polyline(entity) {
      if (entity.kind === "line") return [entity.p0, entity.p1];
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

    function line(points, colour) {
      const object = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points.map(toWorld)),
        new THREE.LineBasicMaterial({ color: host.srgb(colour), depthTest: false })
      );
      object.renderOrder = 1000;
      return object;
    }

    function colourOf(id) {
      if (selection.includes(id)) return COLOURS.selected;
      if (hover && hover.hit && hover.hit.kind === "entity" && hover.hit.reference === id && tool !== "line") return COLOURS.hover;
      return states[id] === "fixed" ? COLOURS.fixed : COLOURS.free;
    }

    let dots = null;
    function render() {
      while (group.children.length) {
        const child = group.children.pop();
        dispose(child);
      }
      for (const entity of sketch.entities) group.add(line(polyline(entity), colourOf(entity.id)));

      const references = sketch.entities.flatMap((entity) => solver.POINTS[entity.kind].map((name) => entity.id + "." + name));
      const dotColours = references.map((reference) => {
        const isHover = hover && hover.hit && hover.hit.kind === "point" && hover.hit.reference === reference;
        return new THREE.Color(host.srgb(selection.includes(reference) ? COLOURS.selected : isHover ? COLOURS.hover : 0xffffff));
      });
      if (references.length) {
        const geometry = new THREE.BufferGeometry().setFromPoints(references.map((reference) => toWorld(solver.pointOf(sketch, reference))));
        geometry.setAttribute("color", new THREE.Float32BufferAttribute(dotColours.flatMap((colour) => [colour.r, colour.g, colour.b]), 3));
        dots = new THREE.Points(geometry, new THREE.PointsMaterial({ size: 7, sizeAttenuation: false, vertexColors: true, depthTest: false }));
        dots.renderOrder = 1001;
        group.add(dots);
      }

      const preview = previewShape();
      if (preview) group.add(line(preview, COLOURS.preview));
      renderLabels();
      renderPanel();
    }

    /// The shape the next click would finish, following the pointer.
    function previewShape() {
      if (!hover || !pending.length) return null;
      const from = pending[0].uv;
      const to = hover.uv;
      if (tool === "line") return [from, to];
      if (tool === "rectangle") return [from, [to[0], from[1]], to, [from[0], to[1]], from];
      if (tool === "circle") {
        const r = Math.hypot(to[0] - from[0], to[1] - from[1]);
        return polyline({ kind: "circle", c: from, r });
      }
      if (tool === "arc") {
        if (pending.length === 1) return [from, to];
        const arc = model.arcThrough(from, pending[1].uv, to);
        return arc ? polyline({ kind: "arc", ...arc }) : [from, pending[1].uv];
      }
      return null;
    }

    // --- Dimension labels -----------------------------------------------------

    let labels = [];
    const VALUE_KINDS = ["distance", "radius", "diameter", "angle"];
    const FORMAT = {
      distance: (value) => value.toFixed(2),
      radius: (value) => "R" + value.toFixed(2),
      diameter: (value) => "⌀" + value.toFixed(2),
      angle: (value) => value.toFixed(1) + "°",
    };

    function renderLabels() {
      labels.forEach((label) => label.element.remove());
      labels = [];
      sketch.constraints.forEach((constraint, index) => {
        if (!VALUE_KINDS.includes(constraint.kind)) return;
        const anchor = model.dimensionAnchor(sketch, constraint);
        if (!anchor) return;
        const element = document.createElement("div");
        element.className = "sketch-dimension-label";
        element.textContent = FORMAT[constraint.kind](constraint.value);
        element.title = "Double-click to change";
        element.dataset.constraint = String(index);
        element.addEventListener("dblclick", (event) => {
          event.stopPropagation();
          openDimension({ constraint, anchor, index });
        });
        ui.labels.appendChild(element);
        labels.push({ element, anchor, index });
      });
    }

    /// Puts each label, and the open box, where its anchor is on screen. Runs
    /// every frame, because the camera can move under them.
    function frame() {
      if (!active) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const place = (element, anchor, dx, dy) => {
        const projected = toWorld(anchor).project(camera);
        element.style.left = rect.left + ((projected.x + 1) / 2) * rect.width + dx + "px";
        element.style.top = rect.top + ((1 - projected.y) / 2) * rect.height + dy + "px";
      };
      labels.forEach((label) => place(label.element, label.anchor, 6, -22));
      if (dimension) place(ui.dimension, dimension.anchor, 6, 6);
    }

    function openDimension(open) {
      dimension = open;
      ui.dimension.style.display = "block";
      ui.dimension.value = open.constraint.value.toFixed(open.constraint.kind === "angle" ? 1 : 2);
      ui.dimension.focus();
      ui.dimension.select();
      setMessage(open.constraint.kind + ": type a value and press Enter");
    }

    function closeDimension() {
      dimension = null;
      selection = [];
      ui.dimension.style.display = "none";
      ui.dimension.blur();
      render();
    }

    function acceptDimension() {
      const value = parseFloat(ui.dimension.value);
      if (!Number.isFinite(value) || value <= 0 && dimension.constraint.kind !== "angle") {
        setMessage("Type a number above zero");
        return;
      }
      const constraint = { ...dimension.constraint, value };
      const next =
        dimension.index === null || dimension.index === undefined
          ? model.withConstraint(sketch, constraint)
          : model.replaceConstraint(sketch, dimension.index, constraint);
      if (change(next)) closeDimension();
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
      if (!gcs) return false;
      const solved = solver.solve(gcs, next, options);
      if (!solved.ok) {
        setMessage("That conflicts with the constraints already there");
        return false;
      }
      sketch = solved.sketch;
      dirty = true;
      refreshStates(options && options.drag);
      message = "";
      render();
      return true;
    }

    /// Which entities are fully constrained, for the colouring. Skipped while
    /// dragging: it costs a solve per point and the colours settle on release.
    function refreshStates(dragging) {
      if (dragging) return;
      const result = solver.entityStates(gcs, sketch);
      states = result.states;
      dof = result.dof;
    }

    // --- Tools -------------------------------------------------------------------

    function setTool(name) {
      if (!TOOL_KEYS_INVERSE.includes(name)) return;
      tool = name;
      pending = [];
      selection = [];
      if (dimension) closeDimension();
      setMessage("");
      render();
    }
    const TOOL_KEYS_INVERSE = Object.values(TOOL_KEYS);

    /// Where a click means: a snapped point if one is near, else the raw position.
    function target(uv, exclude) {
      const snapped = model.snapTarget(sketch, uv, radius(SNAP_PIXELS, uv), exclude);
      return snapped ? { uv: snapped.uv, reference: snapped.reference } : { uv, reference: null };
    }

    function place(event) {
      const raw = pointerUV(event);
      if (!raw) return;
      let point = target(raw);
      if (tool === "line" && pending.length && !point.reference) {
        // Drawing nearly level or upright makes the line exactly so.
        const direction = model.alignment(pending[0].uv, point.uv, ALIGN_DEGREES);
        if (direction === "horizontal") point = { uv: [point.uv[0], pending[0].uv[1]], reference: null, align: direction };
        if (direction === "vertical") point = { uv: [pending[0].uv[0], point.uv[1]], reference: null, align: direction };
      }
      pending.push(point);
      if (pending.length < 1 + PLACING[tool].length) {
        setMessage(PLACING[tool][pending.length - 1]);
        render();
        return;
      }
      finishShape();
    }

    function finishShape() {
      const points = pending;
      let next = null;
      let added = null;
      let joins = [];
      if (tool === "line") {
        added = model.addLine(sketch, points[0].uv, points[1].uv);
        joins = [[points[0].reference, added.id + ".p0"], [points[1].reference, added.id + ".p1"]];
        next = added.sketch;
        if (points[1].align) next = model.withConstraint(next, { kind: points[1].align, e: added.id });
      } else if (tool === "rectangle") {
        added = model.addRectangle(sketch, points[0].uv, points[1].uv);
        next = added && added.sketch;
      } else if (tool === "circle") {
        const r = Math.hypot(points[1].uv[0] - points[0].uv[0], points[1].uv[1] - points[0].uv[1]);
        added = r > 1e-6 ? model.addCircle(sketch, points[0].uv, r) : null;
        next = added && added.sketch;
        if (added) joins = [[points[0].reference, added.id + ".c"]];
      } else if (tool === "arc") {
        added = model.addArc(sketch, points[0].uv, points[1].uv, points[2].uv);
        next = added && added.sketch;
        if (added) {
          // The arc may run the other way round, so join each end to whichever
          // of its own ends is nearer.
          const arc = next.entities.find((entity) => entity.id === added.id);
          const ends = ["p0", "p1"].map((name) => solver.pointOf(next, added.id + "." + name));
          points.slice(0, 2).forEach((point) => {
            const nearer = Math.hypot(ends[0][0] - point.uv[0], ends[0][1] - point.uv[1]) <= Math.hypot(ends[1][0] - point.uv[0], ends[1][1] - point.uv[1]) ? "p0" : "p1";
            joins.push([point.reference, arc.id + "." + nearer]);
          });
        }
      }
      if (!next) {
        pending = [];
        setMessage("That shape has no size");
        render();
        return;
      }
      for (const [existing, fresh] of joins) if (existing) next = model.connect(next, existing, fresh);
      const closedLoop = tool === "line" && points[1].reference && points[1].reference === chainStart;
      if (!change(next)) {
        pending = [];
        render();
        return;
      }
      if (tool === "line" && !closedLoop) {
        if (!chainStart) chainStart = points[0].reference || added.id + ".p0";
        pending = [{ uv: solver.pointOf(sketch, added.id + ".p1"), reference: added.id + ".p1" }];
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
      if (selection.includes(reference)) selection = selection.filter((item) => item !== reference);
      else selection = additive ? [...selection, reference] : [reference];
    }

    function dimensionClick(hit) {
      if (!hit) return;
      toggleSelected(hit.reference, true);
      // A line or circle alone is enough for a dimension. Points and lines
      // want a second pick, and a second line turns the length into an angle.
      const suggestion = model.suggestDimension(sketch, selection);
      if (suggestion) openDimension({ constraint: suggestion.constraint, anchor: suggestion.anchor, index: null });
      else render();
    }

    function selectionUnder(event) {
      const uv = pointerUV(event);
      return uv ? { uv, hit: model.hit(sketch, uv, radius(SNAP_PIXELS, uv)) } : null;
    }

    function applyConstraint(kind) {
      try {
        const constraint = model.constraintFor(sketch, kind, selection);
        if (change(model.withConstraint(sketch, constraint))) selection = [];
      } catch (error) {
        setMessage(error.message);
      }
      render();
    }

    function deleteSelection() {
      const ids = [...new Set(selection.map((reference) => model.entityIdOf(reference)))];
      if (!ids.length) return;
      selection = [];
      change(model.deleteEntities(sketch, ids));
    }

    // --- Pointer and key events, called by the viewer ----------------------------------

    function pointerMove(event) {
      const uv = pointerUV(event);
      if (!uv) return;
      if (press && !press.dragging && Math.hypot(event.clientX - press.x, event.clientY - press.y) > DRAG_PIXELS && press.hit) {
        press.dragging = true;
        host.setCameraEnabled(false);
      }
      if (press && press.dragging) {
        const delta = [uv[0] - press.uv[0], uv[1] - press.uv[1]];
        if (change(sketch, { drag: { ref: press.hit.reference, delta } })) press.uv = uv;
        return;
      }
      const under = tool === "line" || tool === "select" || tool === "dimension" ? model.hit(sketch, uv, radius(SNAP_PIXELS, uv)) : null;
      const snapped = tool === "line" || tool === "rectangle" || tool === "circle" || tool === "arc" ? target(uv) : { uv };
      hover = { uv: snapped.uv, hit: under };
      if (tool === "line" && pending.length && !snapped.reference) {
        const direction = model.alignment(pending[0].uv, uv, ALIGN_DEGREES);
        if (direction === "horizontal") hover.uv = [uv[0], pending[0].uv[1]];
        if (direction === "vertical") hover.uv = [pending[0].uv[0], uv[1]];
      }
      renderer.domElement.style.cursor = under && tool === "select" ? "move" : "crosshair";
      render();
    }

    function pointerDown(event) {
      if (event.button !== 0) return;
      if (dimension) return;
      if (tool === "select") {
        const under = selectionUnder(event);
        if (!under) return;
        press = { x: event.clientX, y: event.clientY, hit: under.hit, uv: under.uv, dragging: false, additive: event.shiftKey };
        return;
      }
      if (tool === "dimension") {
        const under = selectionUnder(event);
        dimensionClick(under && under.hit);
        return;
      }
      place(event);
    }

    function pointerUp() {
      if (!press) return;
      const finished = press;
      press = null;
      if (finished.dragging) {
        host.setCameraEnabled(true);
        refreshStates(false);
        render();
        return;
      }
      if (finished.hit) toggleSelected(finished.hit.reference, finished.additive);
      else if (!finished.additive) selection = [];
      render();
    }

    function doubleClick() {
      if (tool === "line") endChain();
    }

    /// True when the sketcher used the key.
    function keyDown(event) {
      if (event.target === ui.dimension) {
        if (event.key === "Enter") acceptDimension();
        else if (event.key === "Escape") closeDimension();
        else return false;
        event.preventDefault();
        return true;
      }
      if (event.metaKey || event.ctrlKey) return false;
      const key = event.key.toLowerCase();
      if (event.key === "Escape") {
        if (pending.length) endChain();
        else if (selection.length) {
          selection = [];
          render();
        } else if (tool !== "select") setTool("select");
        else if (!sketch.entities.length) host.cancel();
        return true;
      }
      if (event.key === "Enter") {
        if (tool === "line" && pending.length) endChain();
        else finish();
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
        } catch (error) {
          usable = false;
        }
        button.disabled = !usable;
      });
      const constrained = dof === 0 && sketch.entities.length ? "Fully constrained" : dof === null ? "" : dof + " freedom left";
      ui.status.textContent = message || (pending.length ? "" : HINTS[tool]);
      ui.state.textContent = constrained;
    }

    // --- Starting and stopping ---------------------------------------------------------------

    /// Opens the sketcher on `newPlane` ({origin, xDir, yDir, normal} as three.js
    /// vectors). Pass `feature` to redraw an existing entity sketch.
    function begin(newPlane, feature) {
      plane = newPlane;
      editing = feature || null;
      sketch = feature ? { entities: JSON.parse(JSON.stringify(feature.entities)), constraints: JSON.parse(JSON.stringify(feature.constraints || [])) } : model.emptySketch();
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
        if (!active) return;
        if (sketch.entities.length) refreshStates(false);
        // A new sketch starts on the tool people reach for first.
        setTool(editing ? "select" : "line");
      });
      render();
    }

    function end() {
      active = false;
      plane = null;
      editing = null;
      pending = [];
      selection = [];
      dimension = null;
      press = null;
      ui.dimension.style.display = "none";
      labels.forEach((label) => label.element.remove());
      labels = [];
      while (group.children.length) dispose(group.children.pop());
    }

    function finish() {
      if (!sketch.entities.length) {
        host.cancel();
        return;
      }
      const shape = { entities: sketch.entities, constraints: sketch.constraints };
      host.commit(editing ? { type: "update_sketch_entities", id: editing.id, ...shape } : { type: "add_sketch", plane: host.planePayload(plane), ...shape });
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
        message,
        dirty,
        editing: editing ? editing.id : null,
      };
    }

    /// Where [u, v] is on screen, in page pixels, for tests to click.
    function screenPointOfUV(uv) {
      camera.updateMatrixWorld();
      const projected = toWorld(uv).project(camera);
      const rect = renderer.domElement.getBoundingClientRect();
      return { x: rect.left + ((projected.x + 1) / 2) * rect.width, y: rect.top + ((1 - projected.y) / 2) * rect.height };
    }

    ui.tools.forEach((button) => button.addEventListener("click", () => setTool(button.dataset.tool)));
    ui.constraints.forEach((button) => button.addEventListener("click", () => applyConstraint(button.dataset.constraint)));
    ui.finish.addEventListener("click", finish);
    ui.cancel.addEventListener("click", () => host.cancel());

    return {
      begin,
      end,
      frame,
      pointerMove,
      pointerDown,
      pointerUp,
      doubleClick,
      keyDown,
      snapshot,
      screenPointOfUV,
      get active() {
        return active;
      },
      CONSTRAINTS,
    };
  }

  root.createSketcher = createSketcher;
})(typeof window !== "undefined" ? window : globalThis);
