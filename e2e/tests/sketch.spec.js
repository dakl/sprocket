// M3's definition of done, driven through the real viewer against a real
// engine: pick a plane, sketch, dimension, finish, pick a region, extrude.
//
// Clicks land where `__tcad.sketchPoint([u, v])` says a sketch point is, so a
// change to the camera or the panel does not break them. Every assertion on the
// resulting part is the engine's own measurement, never the viewer's.
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers");

const EMPTY = { features: [], rollback_index: 0 };

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, EMPTY);
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad);
  await settled(page);
});

const tool = (page, name) => page.locator(`#sketch-draw-panel [data-tool="${name}"]`);
const constraint = (page, name) => page.locator(`#sketch-draw-panel [data-constraint="${name}"]`);
const sketchState = (page) => page.evaluate(() => window.__tcad.sketch);

async function click(page, uv) {
  const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
  await page.mouse.click(point.x, point.y);
}

/// Starts a sketch on the XY plane and waits until it takes drawing clicks.
async function startSketch(page) {
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(
    () => window.__tcad.sketch.active && window.__tcad.sketch.tool === "line",
  );
  await cameraStill(page);
}

async function drawRectangle(page, from, to) {
  await tool(page, "rectangle").click();
  await click(page, from);
  await click(page, to);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(4);
}

/// Types a value into the dimension box the last click opened.
async function dimension(page, value) {
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  await page.keyboard.type(String(value));
  await page.keyboard.press("Enter");
  await expect(page.locator("#sketch-dimension")).toBeHidden();
}

async function finishSketch(page) {
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(
    () => !window.__tcad.sketch.active && window.__tcad.timeline.length > 0,
  );
  await settled(page);
}

async function extrudeRegion(page, sketchId, index, distance) {
  const point = await page.evaluate(
    ([id, at]) => window.__tcad.regionPoint(id, at),
    [sketchId, index],
  );
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.regions)).toEqual([index]);
  await page.locator("#tool-extrude").click();
  await expect(page.locator("#extrude-panel")).toBeVisible();
  await page.keyboard.type(String(distance));
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !window.__tcad.extruding);
  await settled(page);
}

test("sketch a rectangle, dimension it, finish, and extrude it into the box the rect_extrude golden describes", async ({
  page,
  request,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);

  // Click a side, type its length. The box opens on the measured value, so a
  // dimension typed as it stands would move nothing.
  await tool(page, "dimension").click();
  await click(page, [0, -10]);
  await dimension(page, 40);
  await click(page, [20, 0]);
  await dimension(page, 20);
  const dimensioned = await sketchState(page);
  expect(
    dimensioned.constraints.filter((item) => item.kind === "distance").map((item) => item.value),
  ).toEqual([40, 20]);

  await finishSketch(page);
  const feature = (await rpc(request, "get_document")).features[0];
  expect(feature.type).toBe("sketch");
  expect(feature.entities).toHaveLength(4);

  await extrudeRegion(page, "f1", 0, 10);
  const timeline = await page.evaluate(() => window.__tcad.timeline);
  expect(timeline.map((entry) => [entry.id, entry.type, entry.active, entry.failed])).toEqual([
    ["f1", "sketch", true, false],
    ["f2", "extrude", true, false],
  ]);

  // The same part as tests/golden/rect_extrude: 40 x 20 x 10 with six faces.
  // Where it sits depends on where the corners were clicked, so the position
  // is not asserted.
  const measured = await rpc(request, "measure");
  expect(measured.volume).toBeCloseTo(8000, 3);
  const [min, max] = [measured.bounding_box.min, measured.bounding_box.max];
  expect(
    [max[0] - min[0], max[1] - min[1], max[2] - min[2]].map(
      (size) => Math.round(size * 1000) / 1000,
    ),
  ).toEqual([40, 20, 10]);
  expect((await page.evaluate(() => window.__tcad.mesh)).faces).toBe(6);
});

test("a circle across a side cuts three regions, and picking one extrudes just that one", async ({
  page,
  request,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "circle").click();
  await click(page, [0, 10]);
  await click(page, [6, 10]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(5);
  await finishSketch(page);

  const regions = (await page.evaluate(() => window.__tcad.regions))[0].regions;
  expect(regions).toHaveLength(3);
  const smallest = regions.reduce(
    (best, region, index) => (region.area < regions[best].area ? index : best),
    0,
  );

  await extrudeRegion(page, "f1", smallest, 5);
  const measured = await rpc(request, "measure");
  expect(measured.volume).toBeCloseTo(regions[smallest].area * 5, 1);
  expect(measured.volume).toBeLessThan(400);
  const [extrude] = (await rpc(request, "get_document")).features.slice(-1);
  expect(extrude.profiles).toHaveLength(1);
  expect(extrude.profiles[0].bounded_by).toEqual(regions[smallest].bounded_by);
});

test("a circle inside a circle cuts a hole: picking the ring extrudes a tube, not a disc", async ({
  page,
  request,
}) => {
  await startSketch(page);
  await tool(page, "circle").click();
  await click(page, [0, 0]);
  await click(page, [20, 0]);
  await click(page, [0, 0]);
  await click(page, [10, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(2);
  await finishSketch(page);

  const regions = (await page.evaluate(() => window.__tcad.regions))[0].regions;
  expect(regions).toHaveLength(2);
  const ring = regions.reduce(
    (best, region, index) => (region.area > regions[best].area ? index : best),
    0,
  );
  expect(regions[ring].holes).toHaveLength(1);

  await extrudeRegion(page, "f1", ring, 5);
  const measured = await rpc(request, "measure");
  expect(measured.volume).toBeCloseTo(Math.PI * (20 ** 2 - 10 ** 2) * 5, 0);
});

async function hover(page, uv) {
  const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
  await page.mouse.move(point.x, point.y);
}

const snapOf = async (page) => (await sketchState(page)).snap;

test("the pointer shows what it would snap to: a centre, the origin, a spot on a curve", async ({
  page,
}) => {
  await startSketch(page);
  await tool(page, "circle").click();
  await click(page, [30, 0]);
  await click(page, [40, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(1);
  await tool(page, "line").click();

  const marker = page.locator(".sketch-snap .text");
  await hover(page, [30, 0]);
  expect((await snapOf(page)).name).toBe("Centre");
  await expect(marker).toBeVisible();
  await expect(marker).toContainText("Centre");
  await expect(page.locator(".sketch-snap")).toHaveAttribute("data-kind", "point");

  await hover(page, [0, 0]);
  expect((await snapOf(page)).name).toBe("Origin");
  await expect(marker).toContainText("Origin");

  await hover(page, [30, 10.1]);
  expect((await snapOf(page)).name).toBe("On circle");
  await expect(page.locator(".sketch-snap")).toHaveAttribute("data-kind", "curve");

  await hover(page, [-25, 20]);
  expect(await snapOf(page)).toBeNull();
  await expect(marker).toBeHidden();
});

test("a line drawn from the origin to a circle is joined to both: coincident at one end, on the curve at the other", async ({
  page,
}) => {
  await startSketch(page);
  await tool(page, "circle").click();
  await click(page, [30, 0]);
  await click(page, [40, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(1);
  await tool(page, "line").click();
  await click(page, [0, 0]);
  await click(page, [30, 10.1]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(2);

  const { constraints, entities } = await sketchState(page);
  expect(constraints).toContainEqual(
    expect.objectContaining({ kind: "coincident", a: "origin", b: "e2.p0" }),
  );
  expect(constraints).toContainEqual(
    expect.objectContaining({ kind: "point_on", a: "e2.p1", b: "e1" }),
  );
  expect(Math.hypot(entities[1].p1[0] - 30, entities[1].p1[1])).toBeCloseTo(10, 4);
});

test("the axes are hidden until the browser shows them, and only a shown axis is snapped to", async ({
  page,
}) => {
  await startSketch(page);
  expect(await page.evaluate(() => window.__tcad.axes)).toEqual({ x: false, y: false, z: false });
  await hover(page, [35, 0.3]);
  expect(await snapOf(page)).toBeNull();

  await page.locator(".browser-row", { hasText: "X axis" }).locator(".browser-eye").click();
  expect(await page.evaluate(() => window.__tcad.axes)).toEqual({ x: true, y: false, z: false });
  await hover(page, [35, 0.2]);
  expect((await snapOf(page)).name).toBe("On X axis");
  await hover(page, [0.2, 25]);
  expect(await snapOf(page), "the y axis is still hidden").toBeNull();

  await page.locator(".browser-row", { hasText: "Y axis" }).locator(".browser-eye").click();
  await hover(page, [0.2, 25]);
  expect((await snapOf(page)).name).toBe("On Y axis");

  // A line from the x axis out to the y axis is joined to both, and stays on them.
  await click(page, [35, 0.2]);
  await click(page, [0.2, 25]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(1);
  const { constraints, entities } = await sketchState(page);
  expect(constraints).toContainEqual(
    expect.objectContaining({ kind: "point_on", a: "e1.p0", b: "axis_u" }),
  );
  expect(constraints).toContainEqual(
    expect.objectContaining({ kind: "point_on", a: "e1.p1", b: "axis_v" }),
  );
  expect(entities[0].p0[1]).toBeCloseTo(0, 6);
  expect(entities[0].p1[0]).toBeCloseTo(0, 6);
});

test("constraints are counted: a rectangle has freedom left until it is dimensioned and pinned", async ({
  page,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await expect.poll(async () => (await sketchState(page)).dof).toBeGreaterThan(0);
  expect(Object.values((await sketchState(page)).states)).toEqual(["free", "free", "free", "free"]);

  // Fix a corner: the point under the first line's start.
  await tool(page, "select").click();
  await click(page, [-20, -10]);
  await expect(constraint(page, "fix")).toBeEnabled();
  await constraint(page, "fix").click();

  await tool(page, "dimension").click();
  await click(page, [0, -10]);
  await dimension(page, 40);
  await click(page, [20, 0]);
  await dimension(page, 20);

  await expect.poll(async () => (await sketchState(page)).dof).toBe(0);
  expect(Object.values((await sketchState(page)).states)).toEqual([
    "fixed",
    "fixed",
    "fixed",
    "fixed",
  ]);
  await expect(page.locator("#sketch-state")).toHaveText("Fully constrained");
});

test("a dimension that contradicts the others is refused and the sketch is left as it was", async ({
  page,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "dimension").click();
  await click(page, [0, -10]);
  await dimension(page, 40);
  // The top edge is tied to the bottom one by the rectangle's own constraints
  // only up to its length, so a second, different width on it cannot hold.
  await click(page, [0, 10]);
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  await page.keyboard.type("55");
  await page.keyboard.press("Enter");
  // The refusal names what it ran into, not just that it ran into something.
  await expect(page.locator("#sketch-status")).toContainText("conflicts with distance");
  expect((await sketchState(page)).conflicting.length).toBeGreaterThan(0);
  expect(
    (await sketchState(page)).constraints.filter((item) => item.kind === "distance"),
  ).toHaveLength(1);
});

test("dragging a corner of a rectangle keeps it a rectangle", async ({ page }) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "select").click();
  const from = await page.evaluate(() => window.__tcad.sketchPoint([20, 10]));
  const to = await page.evaluate(() => window.__tcad.sketchPoint([30, 16]));
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 4 });
  await page.mouse.move(to.x, to.y, { steps: 4 });
  await page.mouse.up();

  const { entities } = await sketchState(page);
  const [bottom, right, top, left] = entities;
  expect(Math.abs(bottom.p0[1] - bottom.p1[1])).toBeLessThan(1e-6);
  expect(Math.abs(top.p0[1] - top.p1[1])).toBeLessThan(1e-6);
  expect(Math.abs(right.p0[0] - right.p1[0])).toBeLessThan(1e-6);
  expect(Math.abs(left.p0[0] - left.p1[0])).toBeLessThan(1e-6);
  expect(right.p0[0]).toBeGreaterThan(25);
});

test("redrawing a sketch by double-clicking its chip keeps the extrude's profile, and the part follows", async ({
  page,
  request,
}) => {
  const entities = [
    { id: "e1", kind: "line", p0: [0, 0], p1: [40, 0] },
    { id: "e2", kind: "line", p0: [40, 0], p1: [40, 20] },
    { id: "e3", kind: "line", p0: [40, 20], p1: [0, 20] },
    { id: "e4", kind: "line", p0: [0, 20], p1: [0, 0] },
  ];
  const constraints = [
    { kind: "coincident", a: "e1.p1", b: "e2.p0" },
    { kind: "coincident", a: "e2.p1", b: "e3.p0" },
    { kind: "coincident", a: "e3.p1", b: "e4.p0" },
    { kind: "coincident", a: "e4.p1", b: "e1.p0" },
    { kind: "horizontal", e: "e1" },
    { kind: "horizontal", e: "e3" },
    { kind: "vertical", e: "e2" },
    { kind: "vertical", e: "e4" },
    { kind: "fix", e: "e1.p0" },
  ];
  await resetDocument(request, {
    features: [
      { id: "f1", type: "sketch", plane: "XY", entities, constraints },
      {
        id: "f2",
        type: "extrude",
        profiles: [{ sketch: "f1", bounded_by: ["e1", "e2", "e3", "e4"] }],
        distance: 10,
      },
    ],
    rollback_index: 2,
  });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad.mesh !== null);
  await settled(page);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(8000, 3);

  await page.locator('.timeline-chip[data-feature-id="f1"]').dblclick();
  await page.waitForFunction(
    () => window.__tcad.sketch.active && window.__tcad.sketch.editing === "f1",
  );
  await cameraStill(page);
  await tool(page, "dimension").click();
  await click(page, [20, 0]);
  await dimension(page, 50);
  await finishSketch(page);
  await page.waitForFunction(() =>
    window.__tcad.features[0].entities.some((entity) => entity.p1 && entity.p1[0] === 50),
  );

  // The extrude still names e1..e4, and now builds the wider part.
  await expect
    .poll(async () => (await rpc(request, "measure")).volume)
    .toBeCloseTo(50 * 20 * 10, 3);
  expect(await page.evaluate(() => window.__tcad.timeline.map((entry) => entry.failed))).toEqual([
    false,
    false,
  ]);
});

test("Escape and Cancel throw a sketch away without touching the document", async ({
  page,
  request,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await page.locator("#sketch-cancel").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active);
  expect((await rpc(request, "get_document")).features).toEqual([]);
});

test("lines chained with the line tool snap level, join at their corners, and close into a region", async ({
  page,
}) => {
  await startSketch(page);
  // The first side is drawn a degree off level. Within a few degrees the tool
  // makes it level, and it says so with a constraint.
  await click(page, [0, 0]);
  await click(page, [30, 1]);
  await click(page, [30, 20]);
  await click(page, [0, 20]);
  await click(page, [0, 0]); // back on the first point: closes the loop
  const drawn = await sketchState(page);
  expect(drawn.entities).toHaveLength(4);
  expect(drawn.constraints.some((item) => item.kind === "horizontal" && item.e === "e1")).toBe(
    true,
  );
  // Four corners joined, and the first one sits on the origin.
  expect(
    drawn.constraints.filter((item) => item.kind === "coincident" && item.a !== "origin"),
  ).toHaveLength(4);
  expect(drawn.constraints).toContainEqual(
    expect.objectContaining({ kind: "coincident", a: "origin", b: "e1.p0" }),
  );
  expect(Math.abs(drawn.entities[0].p0[1] - drawn.entities[0].p1[1])).toBeLessThan(1e-6);

  await finishSketch(page);
  const [sketch] = await page.evaluate(() => window.__tcad.regions);
  expect(sketch.regions).toHaveLength(1);
  expect(sketch.regions[0].bounded_by).toEqual(["e1", "e2", "e3", "e4"]);
});

test("a three-point arc closed with a line is a half disc the engine can extrude", async ({
  page,
  request,
}) => {
  await startSketch(page);
  await tool(page, "arc").click();
  await click(page, [-10, 0]);
  await click(page, [10, 0]);
  await click(page, [0, 10]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(1);
  await tool(page, "line").click();
  await click(page, [10, 0]);
  await click(page, [-10, 0]);
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(2);
  await finishSketch(page);

  const [sketch] = await page.evaluate(() => window.__tcad.regions);
  expect(sketch.regions).toHaveLength(1);
  expect(sketch.regions[0].area).toBeCloseTo((Math.PI * 100) / 2, 0);
  await extrudeRegion(page, "f1", 0, 4);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(((Math.PI * 100) / 2) * 4, -1);
});

test("delete removes the selected shape and the constraints that pointed at it", async ({
  page,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "select").click();
  await click(page, [0, -10]);
  await expect.poll(async () => (await sketchState(page)).selection).toEqual(["e1"]);
  await page.keyboard.press("Delete");
  const after = await sketchState(page);
  expect(after.entities.map((entity) => entity.id)).toEqual(["e2", "e3", "e4"]);
  expect(JSON.stringify(after.constraints)).not.toContain('"e1');
});

test("the constraint buttons act on the selection: horizontal levels a line, tangent touches it to a circle", async ({
  page,
}) => {
  await startSketch(page);
  // A slanted line, well past the few degrees the line tool snaps.
  await click(page, [-20, 0]);
  await click(page, [20, 12]);
  await page.keyboard.press("Escape");
  await tool(page, "circle").click();
  await click(page, [0, 30]);
  await click(page, [6, 30]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(2);

  await tool(page, "select").click();
  await click(page, [0, 6]); // the middle of the line
  await expect.poll(async () => (await sketchState(page)).selection).toEqual(["e1"]);
  await expect(constraint(page, "horizontal")).toBeEnabled();
  await expect(constraint(page, "tangent")).toBeDisabled();
  await constraint(page, "horizontal").click();
  let { entities } = await sketchState(page);
  expect(Math.abs(entities[0].p0[1] - entities[0].p1[1])).toBeLessThan(1e-6);

  // Line and circle together: tangent becomes available, and the circle ends
  // up one radius from the line.
  const circle = entities[1];
  // Applying a constraint clears the selection, so pick the line again.
  await click(page, [10, entities[0].p0[1]]);
  await expect.poll(async () => (await sketchState(page)).selection).toEqual(["e1"]);
  await page.keyboard.down("Shift");
  await click(page, [circle.c[0] + circle.r, circle.c[1]]);
  await page.keyboard.up("Shift");
  await expect.poll(async () => (await sketchState(page)).selection.sort()).toEqual(["e1", "e2"]);
  await expect(constraint(page, "tangent")).toBeEnabled();
  await constraint(page, "tangent").click();
  ({ entities } = await sketchState(page));
  const [line, touching] = entities;
  expect(Math.abs(touching.c[1] - line.p0[1])).toBeCloseTo(touching.r, 6);
});

// --- M3.7: the constraint list, and M3.8: a sketch that an agent edits too -------

const glyph = (page, id) => page.locator(`.sketch-glyph[data-constraint-id="${id}"]`);

test("glyphs sit beside constrained geometry, and the toggle hides them", async ({ page }) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  // Four horizontal or vertical constraints. The four corner joins are shown too.
  await expect.poll(async () => (await sketchState(page)).glyphs).toBe(8);
  await page.locator("#sketch-constraints-toggle").click();
  await expect.poll(async () => (await sketchState(page)).glyphs).toBe(0);
  await page.locator("#sketch-constraints-toggle").click();
  await expect.poll(async () => (await sketchState(page)).glyphs).toBe(8);
});

test("selecting a glyph selects its constraint, and Delete removes just that one", async ({
  page,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  const before = await sketchState(page);
  const target = before.constraints.find((item) => item.kind === "horizontal");
  await glyph(page, target.id).click();
  expect((await sketchState(page)).selectedConstraint).toBe(target.id);
  await page.keyboard.press("Delete");
  const after = await sketchState(page);
  expect(after.constraintIds).toEqual(before.constraintIds.filter((id) => id !== target.id));
  expect(after.entities).toHaveLength(4);
  expect(after.dof).toBe(before.dof + 1);
});

test("a dimension is selected with a click, edited with a double-click, and removed with Delete", async ({
  page,
}) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "dimension").click();
  await click(page, [0, -10]);
  await dimension(page, 40);
  await tool(page, "select").click();
  const label = page.locator(".sketch-dimension-label");
  await label.dblclick();
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  await page.keyboard.type("30");
  await page.keyboard.press("Enter");
  await expect(label).toHaveText("30.00");
  await label.click();
  await page.keyboard.press("Delete");
  await expect(label).toHaveCount(0);
  expect(
    (await sketchState(page)).constraints.filter((item) => item.kind === "distance"),
  ).toHaveLength(0);
});

test("undo and redo step back through the sketch's own changes", async ({ page }) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "dimension").click();
  await click(page, [0, -10]);
  await dimension(page, 40);
  const withDimension = (await sketchState(page)).constraintIds.length;
  await page.keyboard.press("Control+z");
  expect((await sketchState(page)).constraintIds).toHaveLength(withDimension - 1);
  await page.keyboard.press("Control+Shift+z");
  expect((await sketchState(page)).constraintIds).toHaveLength(withDimension);
  // And all the way back: the rectangle is one step, so undoing it empties the sketch.
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  expect((await sketchState(page)).entities).toHaveLength(0);
  expect((await sketchState(page)).redoDepth).toBe(2);
});

const RECTANGLE = {
  entities: [
    { id: "e1", kind: "line", p0: [0, 0], p1: [40, 0] },
    { id: "e2", kind: "line", p0: [40, 0], p1: [40, 20] },
    { id: "e3", kind: "line", p0: [40, 20], p1: [0, 20] },
    { id: "e4", kind: "line", p0: [0, 20], p1: [0, 0] },
  ],
  constraints: [
    { kind: "coincident", a: "e1.p1", b: "e2.p0" },
    { kind: "coincident", a: "e2.p1", b: "e3.p0" },
    { kind: "coincident", a: "e3.p1", b: "e4.p0" },
    { kind: "coincident", a: "e4.p1", b: "e1.p0" },
    { kind: "horizontal", e: "e1" },
    { kind: "horizontal", e: "e3" },
    { kind: "vertical", e: "e2" },
    { kind: "vertical", e: "e4" },
    { kind: "fix", e: "e1.p0" },
  ],
};

async function openSavedSketch(page, request) {
  await resetDocument(request, {
    features: [{ id: "f1", type: "sketch", plane: "XY", ...RECTANGLE }],
    rollback_index: 1,
  });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad.features.length === 1);
  await settled(page);
  await page.locator('.timeline-chip[data-feature-id="f1"]').dblclick();
  await page.waitForFunction(
    () => window.__tcad.sketch.active && window.__tcad.sketch.editing === "f1",
  );
  await cameraStill(page);
}

test("a change made in an open sketch reaches the document before Finish, and Cancel puts it back", async ({
  page,
  request,
}) => {
  await openSavedSketch(page, request);
  await tool(page, "dimension").click();
  await click(page, [20, 0]);
  await dimension(page, 50);
  await expect.poll(async () => (await sketchState(page)).synced).toBe(true);
  const saved = (await rpc(request, "get_document")).features[0];
  expect(saved.constraints.filter((item) => item.kind === "distance")).toHaveLength(1);
  expect(saved.entities[0].p1[0]).toBeCloseTo(50, 6);

  await page.locator("#sketch-cancel").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active);
  await settled(page);
  const restored = (await rpc(request, "get_document")).features[0];
  expect(restored.constraints.filter((item) => item.kind === "distance")).toHaveLength(0);
  expect(restored.entities[0].p1[0]).toBeCloseTo(40, 6);
});

test("a dimension an agent adds to a sketch the viewer has open moves it live", async ({
  page,
  request,
}) => {
  await openSavedSketch(page, request);
  const before = (await sketchState(page)).constraintIds;
  const added = await rpc(request, "add_constraint", {
    feature: "f1",
    constraint: { kind: "distance", a: "e1.p0", b: "e1.p1", value: 55 },
    author: "agent:test",
  });
  await expect
    .poll(async () => (await sketchState(page)).constraintIds)
    .toContain(added.constraint_id);
  const after = await sketchState(page);
  expect(after.constraintIds).toHaveLength(before.length + 1);
  expect(after.entities[0].p1[0]).toBeCloseTo(55, 6);
  await expect(
    page.locator(`.sketch-dimension-label[data-constraint-id="${added.constraint_id}"]`),
  ).toHaveText("55.00");
  expect(after.synced).toBe(true);
});

test("an agent's edit and the viewer's next edit both survive", async ({ page, request }) => {
  await openSavedSketch(page, request);
  const added = await rpc(request, "add_constraint", {
    feature: "f1",
    constraint: { kind: "distance", a: "e1.p0", b: "e1.p1", value: 55 },
  });
  await expect
    .poll(async () => (await sketchState(page)).constraintIds)
    .toContain(added.constraint_id);
  await tool(page, "dimension").click();
  await click(page, [55, 10]);
  await dimension(page, 25);
  await expect.poll(async () => (await sketchState(page)).synced).toBe(true);
  const saved = (await rpc(request, "get_document")).features[0].constraints.filter(
    (item) => item.kind === "distance",
  );
  expect(saved.map((item) => item.value).sort()).toEqual([25, 55]);
});

// --- M5: a sketch on a picked face follows the face --------------------------

const BOX = {
  features: [
    { id: "f1", type: "sketch", plane: "XY", rect: { width: 40, height: 30 } },
    { id: "f2", type: "extrude", profile: "f1", distance: 10 },
  ],
  rollback_index: 2,
};

test("pick a face, press Sketch: the sketch sits on that face and follows it when the part grows", async ({
  page,
  request,
}) => {
  await resetDocument(request, BOX);
  await page.reload();
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);

  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(top);
  await page.locator("#tool-sketch").click();
  await page.waitForFunction(() => window.__tcad.sketch.active);
  await cameraStill(page);
  await drawRectangle(page, [-5, -5], [5, 5]);
  await finishSketch(page);

  const sketch = (await rpc(request, "get_document")).features[2];
  expect(sketch.type).toBe("sketch");
  expect(sketch.plane.feature).toBe("f2");
  expect(sketch.plane.face.expected_count).toBe(1);

  await extrudeRegion(page, "f3", 0, 4);
  expect((await rpc(request, "measure")).bounding_box.max[2]).toBeCloseTo(14, 6);

  // Make the box taller: the sketch and its pad ride up with the top face.
  await rpc(request, "apply_edit", {
    edits: [{ op: "update", id: "f2", feature: { ...BOX.features[1], distance: 25 } }],
  });
  expect((await rpc(request, "measure")).bounding_box.max[2]).toBeCloseTo(29, 6);
  await page.waitForFunction(() => window.__tcad.features[1].distance === 25);
  expect((await page.evaluate(() => window.__tcad.timeline)).every((entry) => !entry.failed)).toBe(
    true,
  );
});
