// Projected reference geometry, driven through the real viewer against a real
// engine: the case the feature exists for. Two sketches on one plane, a set
// distance between the left edge of the outer rectangle and the left edge of
// the inner one, and the gap holding when the outer sketch changes.
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers");

const tool = (page, name) => page.locator(`#sketch-draw-panel [data-tool="${name}"]`);
const sketchState = (page) => page.evaluate(() => window.__tcad.sketch);
// A slow CI runner needs longer than the default for a rebuild.
const REBUILD = { timeout: 15_000 };

async function click(page, uv) {
  const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
  await page.mouse.click(point.x, point.y);
}

async function startSketch(page) {
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.tool === "line");
  await cameraStill(page);
}

async function drawRectangle(page, from, to) {
  await tool(page, "rectangle").click();
  await click(page, from);
  await click(page, to);
  await expect.poll(async () => (await sketchState(page)).entities.filter((entity) => !entity.reference).length).toBe(4);
}

async function dimension(page, value) {
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  await page.keyboard.type(String(value));
  await page.keyboard.press("Enter");
  await expect(page.locator("#sketch-dimension")).toBeHidden();
}

async function finishSketch(page, count) {
  await page.locator("#sketch-finish").click();
  await page.waitForFunction((expected) => !window.__tcad.sketch.active && window.__tcad.timeline.length === expected, count);
  await settled(page);
}

const entitiesOf = async (request, id) => (await rpc(request, "get_document")).features.find((feature) => feature.id === id).entities;
const leftEdgeU = (entities) => Math.min(...entities.filter((entity) => !entity.reference && entity.kind === "line").flatMap((entity) => [entity.p0[0], entity.p1[0]]));

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad);
  await settled(page);
});

test("dimension the inner rectangle's left edge from the outer one's, and the gap holds when the outer sketch changes", async ({ page, request }, info) => {
  // The outer rectangle: 80 wide, hung by its bottom right corner so that a
  // wider one grows to the left, which is the edge the gap is measured from.
  await startSketch(page);
  await drawRectangle(page, [-40, -25], [40, 25]);
  await tool(page, "dimension").click();
  await click(page, [0, -25]);
  await dimension(page, 80);
  await tool(page, "select").click();
  await click(page, [40, -25]);
  await page.locator('#sketch-draw-panel [data-constraint="fix"]').click();
  await finishSketch(page, 1);

  // The inner one is a second sketch on the same plane.
  await startSketch(page);
  await drawRectangle(page, [-30, -10], [20, 10]);

  await tool(page, "project").click();
  await click(page, [-40, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.filter((entity) => entity.reference).length).toBe(1);
  const projected = (await sketchState(page)).entities.find((entity) => entity.reference);
  expect(projected.reference).toEqual({ sketch: "f1", entity: "e4" });
  expect(projected.construction).toBe(true);
  // The corner was clicked with a mouse, so the edge is where the pixel put it, a hair off -40.
  expect(projected.p0[0]).toBeCloseTo(-40, 3);
  expect(projected.p1[0]).toBeCloseTo(-40, 3);

  // The reference cannot be dragged away from its source.
  await tool(page, "select").click();
  const from = await page.evaluate(() => window.__tcad.sketchPoint([-40, 5]));
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 60, from.y + 20, { steps: 4 });
  await page.mouse.up();
  const stayed = (await sketchState(page)).entities.find((entity) => entity.reference);
  expect(stayed.p0).toEqual(projected.p0);
  expect(stayed.p1).toEqual(projected.p1);

  // Click the inner left edge, then the projected one: the box measures across.
  await tool(page, "dimension").click();
  await click(page, [-30, 0]);
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  await click(page, [-40, 15]);
  await expect(page.locator("#sketch-dimension")).toHaveValue("10.00");
  await page.screenshot({ path: info.outputPath("dimension-box.png") });
  await dimension(page, 5);
  const gap = (await sketchState(page)).constraints.find((item) => item.kind === "distance_x");
  expect(gap.value).toBe(5);
  await expect(page.locator(".sketch-dimension-label").filter({ hasText: "↔ 5.00" })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("inner-dimensioned.png") });
  await finishSketch(page, 2);

  // The corners were clicked with a mouse, so the edges sit a hair off -40: compare them with each other.
  const outerBefore = leftEdgeU(await entitiesOf(request, "f1"));
  expect(leftEdgeU(await entitiesOf(request, "f2")) - outerBefore).toBeCloseTo(5, 6);
  // The projection stayed out of the profiles: one region, the rectangle.
  expect(await page.evaluate(() => window.__tcad.regions.find((entry) => entry.sketch === "f2").regions.length)).toBe(1);

  // Widen the outer sketch from 80 to 100. Its left edge moves to -60.
  await page.locator('.timeline-chip[data-feature-id="f1"]').dblclick();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.editing === "f1");
  await cameraStill(page);
  const label = page.locator(".sketch-dimension-label");
  await label.dblclick();
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  await page.keyboard.type("100");
  await page.keyboard.press("Enter");
  await expect(label).toHaveText("100.00");
  await finishSketch(page, 2);

  // The outer left edge moved 20 mm out, and the inner one followed it: the gap is still 5.
  await expect.poll(async () => leftEdgeU(await entitiesOf(request, "f1")), REBUILD).toBeCloseTo(outerBefore - 20, 6);
  const outerAfter = leftEdgeU(await entitiesOf(request, "f1"));
  await expect.poll(async () => leftEdgeU(await entitiesOf(request, "f2")) - outerAfter, REBUILD).toBeCloseTo(5, 6);
  const afterwards = await entitiesOf(request, "f2");
  expect(afterwards.find((entity) => entity.reference).p0[0]).toBeCloseTo(outerAfter, 6);

  // Reopen the inner sketch: it shows the moved geometry and the gap label.
  await page.locator('.timeline-chip[data-feature-id="f2"]').dblclick();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.editing === "f2");
  await cameraStill(page);
  await expect(page.locator(".sketch-dimension-label").filter({ hasText: "↔ 5.00" })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("inner-after-outer-widened.png") });
});

test("Delete removes a projection and the dimension that used it, and the same edge can be projected again", async ({ page, request }) => {
  await resetDocument(request, {
    features: [
      { id: "f1", type: "sketch", plane: "XY", entities: [{ id: "e1", kind: "line", p0: [-40, -25], p1: [-40, 25] }], constraints: [] },
    ],
    rollback_index: 1,
  });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad.timeline.length === 1);
  await startSketch(page);
  await drawRectangle(page, [-30, -10], [20, 10]);
  await tool(page, "project").click();
  await click(page, [-40, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.filter((entity) => entity.reference).length).toBe(1);
  // Projecting it a second time says so and adds nothing.
  await click(page, [-40, 0]);
  await expect(page.locator("#sketch-status")).toContainText("already projected");
  expect((await sketchState(page)).entities.filter((entity) => entity.reference)).toHaveLength(1);

  await tool(page, "select").click();
  await click(page, [-40, 0]);
  await page.keyboard.press("Delete");
  await expect.poll(async () => (await sketchState(page)).entities.filter((entity) => entity.reference).length).toBe(0);
  await tool(page, "project").click();
  await click(page, [-40, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.filter((entity) => entity.reference).length).toBe(1);
});

test("X turns the selected shapes into dashed construction that bounds no region, and X again turns them back", async ({ page }, info) => {
  await startSketch(page);
  await drawRectangle(page, [-20, -10], [20, 10]);
  await tool(page, "circle").click();
  await click(page, [0, 0]);
  await click(page, [5, 0]);
  await expect.poll(async () => (await sketchState(page)).entities.length).toBe(5);
  await tool(page, "select").click();
  await click(page, [0, 5]);
  await expect.poll(async () => (await sketchState(page)).selection).toEqual(["e5"]);
  await expect(page.locator("#sketch-construction")).toBeEnabled();
  await page.keyboard.press("x");
  await expect.poll(async () => (await sketchState(page)).entities.find((entity) => entity.id === "e5").construction).toBe(true);
  await page.screenshot({ path: info.outputPath("construction.png") });
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active && window.__tcad.timeline.length === 1);
  await settled(page);
  // The circle bounds no region now, so the rectangle is one region and not two.
  expect(await page.evaluate(() => window.__tcad.regions[0].regions.length)).toBe(1);

  await page.locator('.timeline-chip[data-feature-id="f1"]').dblclick();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.editing === "f1");
  await cameraStill(page);
  await click(page, [0, 5]);
  await page.locator("#sketch-construction").click();
  await expect.poll(async () => (await sketchState(page)).entities.find((entity) => entity.id === "e5").construction).toBeUndefined();
});

test("an edge of the part can be projected, and the gap to it follows when the part grows", async ({ page, request }, info) => {
  const box = (height) => ({
    features: [
      { id: "f1", type: "sketch", plane: "XY", rect: { width: 40, height } },
      { id: "f2", type: "extrude", profile: "f1", distance: 10 },
    ],
    rollback_index: 2,
  });
  await resetDocument(request, box(20));
  await page.reload();
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);

  const { faces, edges } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(top);
  await page.locator("#tool-sketch").click();
  await page.waitForFunction(() => window.__tcad.sketch.active);
  await cameraStill(page);
  await drawRectangle(page, [-12, -5], [10, 5]);

  // The top edge of the part at v = 10, found by where it is, then clicked where the viewer draws it.
  const back = edges.findIndex((edge) => edge.centre[0] === 0 && edge.centre[1] === 10 && edge.centre[2] === 10);
  expect(back).toBeGreaterThanOrEqual(0);
  await tool(page, "project").click();
  const onScreen = await page.evaluate((edge) => window.__tcad.screenPointOf({ edge }), back);
  expect(onScreen.visible).toBe(true);
  await page.mouse.click(onScreen.x, onScreen.y);
  await expect.poll(async () => (await sketchState(page)).entities.filter((entity) => entity.reference).length).toBe(1);
  const projected = (await sketchState(page)).entities.find((entity) => entity.reference);
  expect(projected.reference.feature).toBe("f2");
  expect(projected.reference.edge.select).toBeDefined();
  expect([projected.p0[1], projected.p1[1]]).toEqual([10, 10]);
  expect(Math.abs(projected.p0[0] - projected.p1[0])).toBeCloseTo(40, 6);

  // Dimension the inner rectangle's top edge, at v = 5, from it: the box opens on 5.
  await tool(page, "dimension").click();
  await click(page, [0, 5]);
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  // The box opened on the first pick covers this point: the click must go through it.
  await click(page, [0, 10]);
  await expect(page.locator("#sketch-dimension")).toHaveValue("5.00");
  await dimension(page, 3);
  await page.screenshot({ path: info.outputPath("body-edge-reference.png") });
  await finishSketch(page, 3);

  const topEdgeV = (entities) => Math.max(...entities.filter((entity) => !entity.reference).flatMap((entity) => [entity.p0[1], entity.p1[1]]));
  expect(topEdgeV(await entitiesOf(request, "f3"))).toBeCloseTo(7, 6);

  // The part grows to 30 deep, so its top edge is at v = 15 and the gap of 3 puts the inner edge at 12.
  await rpc(request, "apply_edit", { edits: [{ op: "update", id: "f1", feature: box(30).features[0] }] });
  await expect.poll(async () => topEdgeV(await entitiesOf(request, "f3")), REBUILD).toBeCloseTo(12, 6);
  expect((await entitiesOf(request, "f3")).find((entity) => entity.reference).p0[1]).toBeCloseTo(15, 6);
  expect((await page.evaluate(() => window.__tcad.timeline)).every((entry) => !entry.failed)).toBe(true);
});
