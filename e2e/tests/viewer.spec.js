// M2's definition of done, driven through the real viewer against a real
// engine: open a document, scrub the timeline, edit a feature's parameters by
// form. Assertions read `window.__tcad`, and clicks land where
// `__tcad.screenPointOf` says a face is, so a camera change does not break them.
const { test, expect } = require("@playwright/test");
const fixture = require("../fixtures/box.json");

const { rpc, resetDocument, settled } = require("./helpers");

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, fixture);
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);
});

const chip = (page, id) => page.locator(`.timeline-chip[data-feature-id="${id}"]`);

test("opens the document: the timeline lists its features and the part is built", async ({ page }) => {
  const state = await page.evaluate(() => ({ timeline: window.__tcad.timeline, mesh: window.__tcad.mesh }));
  expect(state.timeline.map((entry) => [entry.id, entry.type, entry.active])).toEqual([
    ["f1", "sketch", true],
    ["f2", "extrude", true],
  ]);
  expect(state.mesh.faces).toBe(6);
});

test("scrubbing the timeline rolls the part back and forward", async ({ page }) => {
  // A live chip rolls back to just before it. Before the extrude there is
  // only a sketch, which builds no solid.
  await chip(page, "f2").click();
  await page.waitForFunction(() => window.__tcad.mesh === null);
  expect(await page.evaluate(() => window.__tcad.timeline.map((entry) => entry.active))).toEqual([true, false]);

  // A greyed-out chip rolls forward to just past it.
  await chip(page, "f2").click();
  await page.waitForFunction(() => window.__tcad.mesh !== null);
  expect(await page.evaluate(() => window.__tcad.timeline.map((entry) => entry.active))).toEqual([true, true]);
});

test("clicking a visible side wall selects the wall, not the sketch behind it", async ({ page, request }) => {
  // The sketch sits on the underside at z=0, and its outline projects behind
  // the walls. Every visible face has to win the click.
  const { faces } = await rpc(request, "describe_geometry");
  const visible = await page.evaluate(
    (count) =>
      Array.from({ length: count }, (_, face) => ({ face, ...window.__tcad.screenPointOf({ face }) })).filter(
        (point) => point.visible
      ),
    faces.length
  );
  expect(visible.length).toBeGreaterThanOrEqual(3);
  for (const target of visible) {
    await page.mouse.click(target.x, target.y);
    await expect
      .poll(() => page.evaluate(() => window.__tcad.selection))
      .toEqual({ face: target.face, edge: null, sketch: null, regions: [] });
    await page.keyboard.press("Escape");
  }
});

test("a selected face is extruded from the toolbar, with no floating panel", async ({ page, request }) => {
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await expect(page.locator("#tool-extrude")).toBeDisabled();
  await page.mouse.click(point.x, point.y);
  await expect(page.locator("#tool-extrude")).toBeEnabled();
  await expect(page.locator("#selection-panel")).toHaveCount(0);
});

test("clicking an edge selects the B-rep edge the engine numbers the same way", async ({ page, request }) => {
  // The mesh's edge ID map has to agree with shape.edges(), because that order
  // is what every later edge-based op will name. Only a top edge that faces
  // the camera is clickable, so take the first of those.
  const { edges } = await rpc(request, "describe_geometry");
  const target = await page.evaluate((candidates) => {
    for (const edge of candidates) {
      const point = window.__tcad.screenPointOf({ edge });
      if (point && point.visible) return { edge, x: point.x, y: point.y };
    }
    return null;
  }, edges.flatMap((edge, index) => (edge.centre[2] === 10 ? [index] : [])));
  expect(target).not.toBeNull();
  await page.mouse.click(target.x, target.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.edge)).toBe(target.edge);
});

test("editing a feature's parameter by form rebuilds the part", async ({ page, request }) => {
  await chip(page, "f2").locator(".timeline-edit").click();
  const distance = page.getByTestId("field-distance");
  await expect(distance).toHaveValue("10");

  await distance.fill("25");
  await distance.press("Enter");
  await page.waitForFunction(() => window.__tcad.features[1].distance === 25);
  await settled(page);

  // The engine's own measurement, not the form's: 40 x 30 x 25.
  const geometry = await rpc(request, "measure");
  expect(geometry.volume).toBeCloseTo(30000, 3);
  // The form stays open on the edited feature, showing the new value.
  await expect(page.getByTestId("field-distance")).toHaveValue("25");
});

test("a value the engine refuses stays in the form with the reason, and the part is untouched", async ({ page, request }) => {
  await chip(page, "f2").locator(".timeline-edit").click();
  const distance = page.getByTestId("field-distance");
  await distance.fill("0");
  await distance.press("Enter");

  await expect(page.getByTestId("properties-error")).toContainText("f2");
  expect((await rpc(request, "get_document")).features[1].distance).toBe(10);
});

test("a number field takes a parameter expression", async ({ page, request }) => {
  await rpc(request, "set_parameter", { name: "height", value: 12 });
  await chip(page, "f2").locator(".timeline-edit").click();
  const distance = page.getByTestId("field-distance");
  await distance.fill("=height * 2");
  await distance.press("Enter");
  await page.waitForFunction(() => window.__tcad.features[1].distance === "=height * 2");
  await settled(page);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(40 * 30 * 24, 3);
});

test("an edit made by another client, such as an agent, reaches the open viewer", async ({ page, request }) => {
  const feature = { ...fixture.features[1], distance: 7 };
  await rpc(request, "apply_edit", { edits: [{ op: "update", id: "f2", feature }] });
  await page.waitForFunction(() => window.__tcad.features[1].distance === 7);
});

test("undo puts back the value before the last form edit", async ({ page }) => {
  await chip(page, "f2").locator(".timeline-edit").click();
  const distance = page.getByTestId("field-distance");
  await distance.fill("18");
  await distance.press("Enter");
  await page.waitForFunction(() => window.__tcad.features[1].distance === 18);

  // Focus leaves the form, as it does when the user clicks the viewport.
  await page.mouse.click(640, 700);
  await page.keyboard.press("Meta+z");
  await page.waitForFunction(() => window.__tcad.features[1].distance === 10);
});

test("a mesh the engine could not write is shown, and clears with the next good build", async ({ page }) => {
  const banner = page.locator("#mesh-error");
  await expect(banner).toBeHidden();

  await page.evaluate(() => window.reloadPart(window.__tcad.revision || 0, false, "ValueError: tessellation disagrees"));
  await expect(banner).toBeVisible();
  await expect(banner).toHaveText("Could not draw the part: ValueError: tessellation disagrees");

  await page.evaluate(() => window.reloadPart(window.__tcad.revision || 0, false, null));
  await expect(banner).toBeHidden();
});

test("every op in the timeline has an icon, a boolean included", async ({ page, request }) => {
  const circle = { id: "f3", type: "sketch", plane: "XY", circle: { radius: 3, center: [0, 0] } };
  const tool = { id: "f4", type: "extrude", profile: "f3", distance: 10 };
  const union = { id: "f5", type: "boolean", operation: "union", base: "f2", tool: "f4" };
  await rpc(request, "apply_edit", {
    edits: [circle, tool, union].map((feature) => ({ op: "insert", feature })),
  });
  await page.waitForFunction(() => window.__tcad.timeline.length === 5);
  await expect(chip(page, "f5").locator(".timeline-icon svg")).toHaveCount(1);
  for (const id of ["f1", "f2", "f3", "f4", "f5"]) {
    await expect(chip(page, id).locator(".timeline-icon")).not.toHaveText("?");
  }

  // A boolean and a fillet both showed "?". An op registered tomorrow fails here
  // until its icon is added, which is cheaper than finding out from a screenshot.
  const ops = Object.keys(await (await request.get("/schemas.json")).json());
  const icons = await page.evaluate(() => Object.keys(FEATURE_ICONS));
  expect(icons).toEqual(expect.arrayContaining(ops));
});

test("the timeline shows icons only, with the id in the tooltip", async ({ page }) => {
  await expect(chip(page, "f2")).toHaveText(/^\s*$/);
  await expect(chip(page, "f2")).toHaveAttribute("title", "f2 (extrude)");
});

test("dragging the playhead rolls the part back and forward", async ({ page }) => {
  const playhead = page.locator("#playhead");
  const box = async (locator) => locator.boundingBox();

  // It starts after the last chip: everything is built.
  const last = await box(chip(page, "f2"));
  const start = await box(playhead);
  expect(start.x + start.width / 2).toBeGreaterThan(last.x + last.width - 1);

  // Drag it into the gap between the sketch and the extrude. A sketch alone
  // builds no solid, so the part disappears.
  const first = await box(chip(page, "f1"));
  const grab = { x: start.x + start.width / 2, y: start.y + start.height / 2 };
  await page.mouse.move(grab.x, grab.y);
  await page.mouse.down();
  await page.mouse.move(first.x + first.width + 2, grab.y, { steps: 6 });
  await page.mouse.up();
  await page.waitForFunction(() => window.__tcad.mesh === null);
  expect(await page.evaluate(() => window.__tcad.timeline.map((entry) => entry.active))).toEqual([true, false]);

  // The playhead now sits between the two chips.
  const moved = await box(playhead);
  const centre = moved.x + moved.width / 2;
  expect(centre).toBeGreaterThan(first.x + first.width);
  expect(centre).toBeLessThan(last.x);

  // And back to the end: the part is built again.
  await page.mouse.move(centre, grab.y);
  await page.mouse.down();
  await page.mouse.move(last.x + last.width + 20, grab.y, { steps: 6 });
  await page.mouse.up();
  await page.waitForFunction(() => window.__tcad.mesh !== null);
  expect(await page.evaluate(() => window.__tcad.timeline.map((entry) => entry.active))).toEqual([true, true]);
});
