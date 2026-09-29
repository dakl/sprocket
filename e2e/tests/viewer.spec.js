// M2's definition of done, driven through the real viewer against a real
// engine: open a document, scrub the timeline, edit a feature's parameters by
// form. Assertions read `window.__tcad`, and clicks land where
// `__tcad.screenPointOf` says a face is, so a camera change does not break them.
const { test, expect } = require("@playwright/test");
const fixture = require("../fixtures/box.json");

async function rpc(request, method, params = {}) {
  const response = await request.post("/rpc", { data: { jsonrpc: "2.0", id: 1, method, params } });
  const body = await response.json();
  if (body.error) throw new Error(body.error.message);
  return body.result;
}

/// Puts the shared document back to the fixture, through the same edit API an
/// agent uses. The server outlives the tests, so each one starts from here.
async function resetDocument(request) {
  const current = await rpc(request, "get_document");
  const edits = [
    ...current.features.map((feature) => ({ op: "delete", id: feature.id })).reverse(),
    ...fixture.features.map((feature) => ({ op: "insert", feature })),
    { op: "set_rollback", index: fixture.rollback_index },
  ];
  await rpc(request, "apply_edit", { edits });
  for (const name of Object.keys(current.parameters)) {
    await rpc(request, "set_parameter", { name, value: null });
  }
}

async function settled(page) {
  await page.waitForFunction(() => window.__tcad && window.__tcad.idle);
}

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request);
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
      .toEqual({ face: target.face, edge: null, sketch: null });
    await page.keyboard.press("Escape");
  }
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
