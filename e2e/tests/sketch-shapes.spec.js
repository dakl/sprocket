// The shape tools in the sketcher: Center rect, 3-pt circle, Tangent arc, Slot
// and Polygon, and the Concentric and Collinear constraints. Each is drawn by
// clicks at `__tcad.sketchPoint`, then checked in the sketch and, once
// finished, in the engine's own regions.
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers.js");

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.goto("/index.html");
  await page.waitForFunction(() => globalThis.__tcad && globalThis.__tcad.mesh === null);
  await settled(page);
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => globalThis.__tcad.sketch.active && globalThis.__tcad.sketch.tool === "line");
  await cameraStill(page);
});

const sketchState = (page) => page.evaluate(() => globalThis.__tcad.sketch);
const entities = async (page) => (await sketchState(page)).entities;
const entityCount = async (page) => (await entities(page)).length;
const useTool = (page, name) => page.locator(`#sketch-draw-panel [data-tool="${name}"]`).click();
const length = (line) => Math.hypot(line.p1[0] - line.p0[0], line.p1[1] - line.p0[1]);

async function click(page, uv) {
  const point = await page.evaluate((target) => globalThis.__tcad.sketchPoint(target), uv);
  await page.mouse.click(point.x, point.y);
}

/// Finishes the sketch and returns the engine's regions for it.
async function finish(page, request) {
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(() => !globalThis.__tcad.sketch.active && globalThis.__tcad.timeline.length === 1);
  const saved = (await rpc(request, "get_document")).features[0];
  return (await rpc(request, "get_profiles", { feature: saved.id })).profiles;
}

test("Center rect on the origin is centred there by its diagonals, and keeps two freedoms", async ({ page, request }, info) => {
  await useTool(page, "center-rectangle");
  await click(page, [0, 0]);
  await click(page, [20, 10]);
  await expect.poll(() => entityCount(page)).toBe(6);
  const state = await sketchState(page);
  const construction = state.entities.filter((entity) => entity.construction);
  expect(construction).toHaveLength(2);
  expect(state.constraints.filter((item) => item.kind === "point_on" && item.a === "origin").map((item) => item.b).sort()).toEqual(construction.map((entity) => entity.id).sort());
  // Width and height are all that is left.
  await expect.poll(async () => (await sketchState(page)).dof).toBe(2);
  await page.screenshot({ path: info.outputPath("center-rectangle.png") });
  const profiles = await finish(page, request);
  expect(profiles).toHaveLength(1);
  expect(profiles[0].area).toBeCloseTo(40 * 20, 3);
});

test("Polygon takes its side count from the box and draws a regular shape", async ({ page, request }, info) => {
  await page.locator("#sketch-polygon-sides").fill("5");
  await useTool(page, "polygon");
  await click(page, [0, 0]);
  await click(page, [10, 0]);
  await expect.poll(() => entityCount(page)).toBe(6);
  const lines = (await entities(page)).filter((entity) => entity.kind === "line");
  expect(lines).toHaveLength(5);
  for (const line of lines) expect(length(line)).toBeCloseTo(2 * 10 * Math.sin(Math.PI / 5), 4);
  await page.screenshot({ path: info.outputPath("polygon.png") });
  const profiles = await finish(page, request);
  // A regular pentagon of circumradius 10.
  expect(profiles[0].area).toBeCloseTo((5 / 2) * 100 * Math.sin((2 * Math.PI) / 5), 3);
});

test("Slot is two centres and a width, and the engine finds its stadium region", async ({ page, request }, info) => {
  await useTool(page, "slot");
  await click(page, [-10, 0]);
  await click(page, [10, 0]);
  await click(page, [0, 4]);
  await expect.poll(() => entityCount(page)).toBe(6);
  const arcs = (await entities(page)).filter((entity) => entity.kind === "arc");
  expect(arcs.map((arc) => arc.r)).toEqual([4, 4].map((value) => expect.closeTo(value, 4)));
  // Two centres and a width.
  await expect.poll(async () => (await sketchState(page)).dof).toBe(5);
  await page.screenshot({ path: info.outputPath("slot.png") });
  const profiles = await finish(page, request);
  expect(profiles).toHaveLength(1);
  expect(profiles[0].area).toBeCloseTo(20 * 8 + Math.PI * 16, 2);
});

test("3-pt circle passes through three clicks", async ({ page }) => {
  await useTool(page, "circle-3");
  await click(page, [10, 0]);
  await click(page, [0, 10]);
  await click(page, [-10, 0]);
  await expect.poll(() => entityCount(page)).toBe(1);
  const [circle] = await entities(page);
  expect(circle.kind).toBe("circle");
  expect(circle.r).toBeCloseTo(10, 4);
  expect(circle.c[0]).toBeCloseTo(0, 4);
  expect(circle.c[1]).toBeCloseTo(0, 4);
});

test("Tangent arc carries on from a line's end and refuses to start in mid-air", async ({ page }, info) => {
  await useTool(page, "line");
  await click(page, [0, 0]);
  await click(page, [20, 0]);
  await page.keyboard.press("Escape");
  await expect.poll(() => entityCount(page)).toBe(1);
  await useTool(page, "tangent-arc");
  await click(page, [5, 15]);
  await expect(page.locator("#sketch-status")).toContainText("end of a line or arc");
  await click(page, [20, 0]);
  await click(page, [20, 20]);
  await expect.poll(() => entityCount(page)).toBe(3);
  const arc = (await entities(page)).find((entity) => entity.kind === "arc");
  // Square above the joint: centre (20, 10), radius 10.
  expect(arc.c[0]).toBeCloseTo(20, 4);
  expect(arc.c[1]).toBeCloseTo(10, 4);
  expect(arc.r).toBeCloseTo(10, 4);
  expect((await sketchState(page)).conflicting).toEqual([]);
  await page.screenshot({ path: info.outputPath("tangent-arc.png") });
});

async function shiftClick(page, uv) {
  const point = await page.evaluate((target) => globalThis.__tcad.sketchPoint(target), uv);
  await page.keyboard.down("Shift");
  await page.mouse.click(point.x, point.y);
  await page.keyboard.up("Shift");
}

test("Concentric gives two circles one centre; Collinear puts two lines on one line", async ({ page }) => {
  await useTool(page, "circle");
  await click(page, [0, 0]);
  await click(page, [10, 0]);
  await click(page, [3, 2]);
  await click(page, [8, 2]);
  await expect.poll(() => entityCount(page)).toBe(2);
  await useTool(page, "select");
  await click(page, [0, -10]);
  await shiftClick(page, [3, 7]);
  await expect.poll(async () => (await sketchState(page)).selection.sort()).toEqual(["e1", "e2"]);
  await expect(page.locator('[data-constraint="concentric"]')).toBeEnabled();
  await expect(page.locator('[data-constraint="collinear"]')).toBeDisabled();
  await page.locator('[data-constraint="concentric"]').click();
  await expect.poll(async () => {
    const [first, second] = await entities(page);
    return Math.hypot(second.c[0] - first.c[0], second.c[1] - first.c[1]);
  }).toBeLessThan(1e-6);

  await useTool(page, "line");
  await click(page, [-30, 20]);
  await click(page, [-20, 20]);
  await page.keyboard.press("Escape");
  await click(page, [-15, 23]);
  await click(page, [-5, 23]);
  await page.keyboard.press("Escape");
  await expect.poll(() => entityCount(page)).toBe(4);
  await useTool(page, "select");
  await click(page, [-25, 20]);
  await shiftClick(page, [-10, 23]);
  await page.locator('[data-constraint="collinear"]').click();
  await expect.poll(async () => {
    const lines = (await entities(page)).filter((entity) => entity.kind === "line");
    return Math.abs(lines[1].p0[1] - lines[0].p0[1]) + Math.abs(lines[1].p1[1] - lines[0].p0[1]);
  }).toBeLessThan(1e-6);
});

test("an agent draws a slot through agent_pointer, clicking the button and the canvas as a person would", async ({ page, request }) => {
  const answer = await rpc(request, "agent_pointer", { action: "click", target: '#sketch-draw-panel [data-tool="slot"]' });
  expect(answer.element).toMatch(/^button.*"Slot:/);
  await expect.poll(async () => (await sketchState(page)).tool).toBe("slot");
  for (const uv of [[-10, 0], [10, 0], [0, 4]]) {
    const point = await page.evaluate((target) => globalThis.__tcad.sketchPoint(target), uv);
    expect((await rpc(request, "agent_pointer", { action: "click", x: point.x, y: point.y })).element).toBe("canvas");
  }
  await expect.poll(() => entityCount(page)).toBe(6);
  await expect(page.locator("#agent-pointer")).toHaveClass(/shown/);
});
