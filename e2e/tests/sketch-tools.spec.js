// Trim, Extend and Offset in the sketcher, driven by clicks. Each click lands
// where `__tcad.sketchPoint` says a sketch point is on screen.
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers");

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh === null);
  await settled(page);
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(
    () => window.__tcad.sketch.active && window.__tcad.sketch.tool === "line",
  );
  await cameraStill(page);
});

const sketchState = (page) => page.evaluate(() => window.__tcad.sketch);
const entity = async (page, id) =>
  (await sketchState(page)).entities.find((item) => item.id === id);

async function at(page, uv) {
  const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
  return point;
}
async function click(page, uv) {
  const point = await at(page, uv);
  await page.mouse.click(point.x, point.y);
}

async function drawLine(page, from, to) {
  await page.locator('#sketch-draw-panel [data-tool="line"]').click();
  await click(page, from);
  await click(page, to);
  await page.keyboard.press("Escape");
}

const useTool = (page, name) => page.locator(`#sketch-draw-panel [data-tool="${name}"]`).click();
const entityCount = async (page) => (await sketchState(page)).entities.length;

test("Trim removes the piece of a line between two crossings, and Extend runs a line on to a wall", async ({
  page,
}, info) => {
  await drawLine(page, [-30, 0], [30, 0]);
  await drawLine(page, [-10, -20], [-10, 20]);
  await drawLine(page, [10, -20], [10, 20]);
  await expect.poll(() => entityCount(page)).toBe(3);

  await useTool(page, "trim");
  await click(page, [0, 0]);
  await expect.poll(() => entityCount(page)).toBe(4);
  await page.screenshot({ path: info.outputPath("trim.png") });
  const [left, right] = [await entity(page, "e1"), await entity(page, "e4")];
  expect(left.p0[0]).toBeCloseTo(-30, 4);
  expect(left.p1[0]).toBeCloseTo(-10, 4);
  expect(right.p0[0]).toBeCloseTo(10, 4);
  expect(right.p1[0]).toBeCloseTo(30, 4);
  // Each cut end stays on the line that cut it.
  const constraints = (await sketchState(page)).constraints;
  expect(
    constraints
      .filter((item) => item.kind === "point_on")
      .map((item) => [item.a, item.b])
      .sort(),
  ).toEqual([
    ["e1.p1", "e2"],
    ["e4.p0", "e3"],
  ]);

  // The upright line at x = -10 is crossed once, at y = 0: trimming its top leaves the lower half.
  await click(page, [-10, 15]);
  await expect.poll(async () => (await entity(page, "e2")).p1[1]).toBeCloseTo(0, 4);
  expect(await entityCount(page)).toBe(4);

  // The wall at x = 10 is e3. Extend the left piece's right end across the gap to it.
  await useTool(page, "extend");
  await click(page, [-11, 0]);
  await expect.poll(async () => (await entity(page, "e1")).p1[0]).toBeCloseTo(10, 4);
  await page.screenshot({ path: info.outputPath("extend.png") });
  // The far end has nothing in its way, and the line is unchanged.
  await click(page, [-29, 0]);
  await expect(page.locator("#sketch-status")).toContainText("Nothing in the way");
  expect((await entity(page, "e1")).p0[0]).toBeCloseTo(-30, 4);
});

test("Offset moves a rectangle out by a typed distance, and the copy is saved with the sketch", async ({
  page,
  request,
}, info) => {
  await useTool(page, "rectangle");
  await click(page, [0, 0]);
  await click(page, [40, 20]);
  await expect.poll(() => entityCount(page)).toBe(4);

  await useTool(page, "offset");
  await click(page, [20, 0]);
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  // Move below the bottom edge: the outside. The box follows the pointer's distance.
  const outside = await at(page, [20, -3]);
  await page.mouse.move(outside.x, outside.y);
  await expect(page.locator("#sketch-dimension")).toHaveValue("3");
  await page.keyboard.type("4");
  await expect(page.locator("#sketch-dimension")).toHaveValue("4");
  await page.screenshot({ path: info.outputPath("offset-preview.png") });
  await page.keyboard.press("Enter");
  await expect(page.locator("#sketch-dimension")).toBeHidden();
  await expect.poll(() => entityCount(page)).toBe(8);

  const lines = (await sketchState(page)).entities.slice(4);
  const xs = lines.flatMap((item) => [item.p0[0], item.p1[0]]);
  const ys = lines.flatMap((item) => [item.p0[1], item.p1[1]]);
  expect(
    [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)].map(
      (value) => Math.round(value * 1e4) / 1e4,
    ),
  ).toEqual([-4, 44, -4, 24]);
  await page.screenshot({ path: info.outputPath("offset.png") });

  await page.locator("#sketch-finish").click();
  await page.waitForFunction(
    () => !window.__tcad.sketch.active && window.__tcad.timeline.length === 1,
  );
  const saved = (await rpc(request, "get_document")).features[0];
  expect(saved.entities).toHaveLength(8);
  // The offset ring is a region of its own: the engine finds two nested outlines.
  expect(
    (await rpc(request, "get_profiles", { feature: saved.id })).profiles.length,
  ).toBeGreaterThanOrEqual(2);
});

test("Offset of a circle goes inside when the pointer does, and an offset that cannot fit says so", async ({
  page,
}) => {
  await useTool(page, "circle");
  await click(page, [0, 0]);
  await click(page, [10, 0]);
  await expect.poll(() => entityCount(page)).toBe(1);
  await useTool(page, "offset");
  await click(page, [10, 0]);
  const inside = await at(page, [6, 0]);
  await page.mouse.move(inside.x, inside.y);
  await page.keyboard.type("3");
  await page.keyboard.press("Enter");
  await expect.poll(() => entityCount(page)).toBe(2);
  expect((await entity(page, "e2")).r).toBeCloseTo(7, 4);

  await click(page, [10, 0]);
  const again = await at(page, [6, 0]);
  await page.mouse.move(again.x, again.y);
  await page.keyboard.type("30");
  await page.keyboard.press("Enter");
  await expect(page.locator("#sketch-status")).toContainText("bigger than the radius");
  expect(await entityCount(page)).toBe(2);
});
