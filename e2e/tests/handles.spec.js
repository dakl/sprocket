// The Fusion-style flow for the 3D tools: with nothing selected a tool asks
// for its pick and lights up what it accepts under the pointer; once it has
// one, an arrow (a distance) or a ring (an angle) sets the value by drag, and
// the box beside it takes a typed one. `__tcad.handle` says where to grab the
// handle and which way to drag it, so no test guesses pixels.
const { test, expect } = require("@playwright/test");
const fixture = require("../fixtures/box.json");

const { rpc, resetDocument, settled } = require("./helpers");

const SLOW = { timeout: 15_000 };

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, fixture);
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);
});

const handle = (page) => page.evaluate(() => window.__tcad.handle);
const extruding = (page) => page.evaluate(() => window.__tcad.extruding);

/// Drags the open handle from its grab point toward `__tcad.handle.toward`,
/// `fraction` of the way. Returns the value before the drag.
async function dragHandle(page, fraction = 1) {
  const before = await handle(page);
  expect(before).not.toBeNull();
  await page.mouse.move(before.grab.x, before.grab.y);
  await page.mouse.down();
  const x = before.grab.x + (before.toward.x - before.grab.x) * fraction;
  const y = before.grab.y + (before.toward.y - before.grab.y) * fraction;
  await page.mouse.move(x, y, { steps: 8 });
  await page.mouse.up();
  return before.value;
}

async function faceWithNormal(page, request, normal) {
  const { faces } = await rpc(request, "describe_geometry");
  const face = faces.findIndex((item) => item.normal && item.normal.every((value, axis) => Math.abs(value - normal[axis]) < 1e-6));
  const point = await page.evaluate((index) => window.__tcad.screenPointOf({ face: index }), face);
  expect(point.visible).toBe(true);
  return { face, ...point };
}

async function topEdge(page, request) {
  const { edges } = await rpc(request, "describe_geometry");
  const found = await page.evaluate((candidates) => {
    for (const edge of candidates) {
      const point = window.__tcad.screenPointOf({ edge });
      if (point && point.visible) return { edge, x: point.x, y: point.y };
    }
    return null;
  }, edges.flatMap((edge, index) => (edge.centre[2] === 10 ? [index] : [])));
  expect(found).not.toBeNull();
  return found;
}

test("E with nothing selected asks for a face, lights it up on hover, and a click shows the arrow at 0", async ({ page, request }) => {
  const top = await faceWithNormal(page, request, [0, 0, 1]);
  await expect(page.locator("#tool-extrude")).toBeEnabled();
  await page.keyboard.press("e");
  await expect.poll(() => page.evaluate(() => window.__tcad.picking)).toBe("extrude");
  await expect(page.locator("#hint")).toContainText("pick a face or a sketch region");

  await page.mouse.move(top.x, top.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.hovered)).toEqual({ kind: "face", face: top.face });

  await page.mouse.click(top.x, top.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.picking)).toBeNull();
  expect(await extruding(page)).toEqual({ distance: 0, regions: 0 });
  expect(await handle(page)).toMatchObject({ kind: "linear", value: 0, box: "0 mm" });
  await expect(page.locator("#handle-value")).toBeVisible();
  await expect(page.locator("#handle-value")).toBeFocused();
});

test("dragging the extrude arrow sets the distance, and Enter applies what the drag set", async ({ page, request }) => {
  const top = await faceWithNormal(page, request, [0, 0, 1]);
  await page.mouse.click(top.x, top.y);
  await page.keyboard.press("e");
  await expect.poll(() => handle(page)).not.toBeNull();
  const cameraBefore = await page.evaluate(() => window.__tcad.camera);

  await dragHandle(page);
  const dragged = (await extruding(page)).distance;
  expect(dragged).toBeGreaterThan(0);
  expect((await handle(page)).box).toBe(dragged + " mm");
  // The drag moved the arrow, not the camera.
  const cameraAfter = await page.evaluate(() => window.__tcad.camera);
  cameraAfter.position.forEach((value, axis) => expect(value).toBeCloseTo(cameraBefore.position[axis], 6));

  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.__tcad.timeline.length === 3, null, SLOW);
  await settled(page);
  const pushed = (await rpc(request, "get_document")).features[2];
  expect(pushed.type).toBe("extrude_face");
  expect(pushed.distance).toBe(dragged);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(40 * 30 * (10 + dragged), 2);
  expect(await handle(page)).toBeNull();
});

test("a typed distance moves the arrow, and Enter at 0 says what is missing instead of applying", async ({ page, request }) => {
  const top = await faceWithNormal(page, request, [0, 0, 1]);
  await page.mouse.click(top.x, top.y);
  await page.keyboard.press("e");
  await expect.poll(() => handle(page)).not.toBeNull();
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => window.__tcad.status)).toContain("Drag the arrow or type a distance");
  expect(await extruding(page)).not.toBeNull();

  await page.keyboard.type("-3");
  expect((await handle(page)).value).toBe(-3);
  expect((await extruding(page)).distance).toBe(-3);
  await page.keyboard.press("Escape");
  expect(await extruding(page)).toBeNull();
  expect(await handle(page)).toBeNull();
});

test("the camera orbits while extrude is open, and a click on another face moves the extrude there", async ({ page, request }) => {
  const top = await faceWithNormal(page, request, [0, 0, 1]);
  await page.mouse.click(top.x, top.y);
  await page.keyboard.press("e");
  await expect.poll(() => handle(page)).not.toBeNull();
  await page.keyboard.type("4");

  // A drag on empty space orbits and leaves the tool open.
  const cameraBefore = await page.evaluate(() => window.__tcad.camera);
  const viewport = page.viewportSize();
  await page.mouse.move(viewport.width - 60, viewport.height / 2);
  await page.mouse.down();
  await page.mouse.move(viewport.width - 160, viewport.height / 2 + 40, { steps: 6 });
  await page.mouse.up();
  await expect.poll(() => page.evaluate(() => window.__tcad.camera.position)).not.toEqual(cameraBefore.position);
  expect((await extruding(page)).distance).toBe(4);

  // A click picks the side wall: the extrude follows it and keeps its distance.
  const side = await page.evaluate(() => {
    for (let face = 0; face < window.__tcad.mesh.faces; face++) {
      const point = window.__tcad.screenPointOf({ face });
      if (point.visible && face !== window.__tcad.selection.face) return { face, ...point };
    }
    return null;
  });
  await page.mouse.click(side.x, side.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(side.face);
  expect((await extruding(page)).distance).toBe(4);
  expect((await handle(page)).value).toBe(4);
});

test("F with nothing selected picks an edge; its arrow drives the radius field, and Shift-click adds an edge", async ({ page, request }) => {
  const edge = await topEdge(page, request);
  await expect(page.locator("#tool-fillet")).toBeEnabled();
  await page.keyboard.press("f");
  await expect.poll(() => page.evaluate(() => window.__tcad.picking)).toBe("fillet");
  await page.mouse.move(edge.x, edge.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.hovered)).toEqual({ kind: "edge", edge: edge.edge });
  await page.mouse.click(edge.x, edge.y);
  await expect(page.locator("#modify-panel")).toBeVisible();
  await expect.poll(() => handle(page)).not.toBeNull();
  const start = (await handle(page)).value;
  await expect(page.locator("#modify-radius")).toHaveValue(String(start));

  await dragHandle(page, 0.3);
  const radius = (await handle(page)).value;
  expect(radius).toBeGreaterThan(start);
  await expect(page.locator("#modify-radius")).toHaveValue(String(radius));
  await page.waitForFunction((wanted) => window.__tcad.modifying.feature && window.__tcad.modifying.feature.radius === wanted, radius, SLOW);

  // A second top edge with Shift joins the first, and the radius stays.
  const { edges } = await rpc(request, "describe_geometry");
  const second = await page.evaluate(([candidates, first]) => {
    for (const index of candidates) {
      if (index === first) continue;
      const point = window.__tcad.screenPointOf({ edge: index });
      if (point && point.visible) return { edge: index, x: point.x, y: point.y };
    }
    return null;
  }, [edges.flatMap((item, index) => (item.centre[2] === 10 ? [index] : [])), edge.edge]);
  await page.keyboard.down("Shift");
  await page.mouse.click(second.x, second.y);
  await page.keyboard.up("Shift");
  await expect.poll(() => page.evaluate(() => window.__tcad.selectedEdges)).toEqual([edge.edge, second.edge]);
  await expect.poll(() => page.evaluate(() => window.__tcad.modifying && window.__tcad.modifying.feature && window.__tcad.modifying.feature.edges.expected_count), SLOW).toBe(2);
  await expect(page.locator("#modify-radius")).toHaveValue(String(radius));
  await page.keyboard.press("Escape");
  expect(await handle(page)).toBeNull();
});

test("H picks the face to drill, and the hole's arrow sets its depth", async ({ page, request }) => {
  const top = await faceWithNormal(page, request, [0, 0, 1]);
  await page.keyboard.press("h");
  await expect.poll(() => page.evaluate(() => window.__tcad.picking)).toBe("hole");
  await page.mouse.click(top.x, top.y);
  await expect.poll(() => handle(page)).not.toBeNull();
  const depth = await page.locator("#modify-depth").inputValue();
  expect((await handle(page)).value).toBe(Number(depth));
  await dragHandle(page, 0.5);
  const deeper = (await handle(page)).value;
  expect(deeper).toBeGreaterThan(Number(depth));
  await expect(page.locator("#modify-depth")).toHaveValue(String(deeper));
  // Through has no depth, so no arrow.
  await page.locator("#modify-through").check();
  await expect.poll(() => handle(page)).toBeNull();
  await page.keyboard.press("Escape");
});

test("the circular pattern's ring sets the angle, and a typed panel value turns the ring", async ({ page }) => {
  await page.locator("#tool-circular").click();
  await expect.poll(() => handle(page)).not.toBeNull();
  expect(await handle(page)).toMatchObject({ kind: "angular", value: 360, box: "360°" });
  await dragHandle(page, 0.6);
  const angle = (await handle(page)).value;
  expect(angle).toBeLessThan(360);
  expect(angle).toBeGreaterThan(0);
  await expect(page.locator("#modify-angle")).toHaveValue(String(angle));

  await page.locator("#modify-angle").fill("90");
  expect((await handle(page)).value).toBe(90);
  await page.keyboard.press("Escape");
});

test("offset plane, linear pattern and shell open with an arrow on the value they size", async ({ page }) => {
  for (const [button, field] of [["#tool-offset-plane", "#modify-offset"], ["#tool-linear", "#modify-spacing"], ["#tool-shell", "#modify-thickness"]]) {
    await page.locator(button).click();
    await expect.poll(() => handle(page)).not.toBeNull();
    const shown = await handle(page);
    expect(shown.kind).toBe("linear");
    await expect(page.locator(field)).toHaveValue(String(shown.value));
    await dragHandle(page, 0.4);
    const moved = (await handle(page)).value;
    expect(moved).not.toBe(shown.value);
    await expect(page.locator(field)).toHaveValue(String(moved));
    await page.keyboard.press("Escape");
    await expect.poll(() => handle(page)).toBeNull();
  }
});

test("E with nothing selected picks a sketch region under the pointer, ahead of the face it lies on", async ({ page, request }) => {
  // A circle drawn on the box's top face.
  await resetDocument(request, {
    features: [...fixture.features, { id: "f3", type: "sketch", plane: { name: "XY", offset: 10 }, entities: [{ id: "e1", kind: "circle", c: [5, 0], r: 5 }], constraints: [] }],
    rollback_index: 3,
  });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null && window.__tcad.regions.some((entry) => entry.sketch === "f3"));
  await settled(page);
  const point = await page.evaluate(() => window.__tcad.regionPoint("f3", 0));

  await page.keyboard.press("e");
  await expect.poll(() => page.evaluate(() => window.__tcad.picking)).toBe("extrude");
  await page.mouse.move(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.hovered)).toEqual({ kind: "region", sketch: "f3", region: 0 });
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => extruding(page)).toEqual({ distance: 0, regions: 1 });
  await page.keyboard.type("4");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.__tcad.timeline.length === 4, null, SLOW);
  await settled(page);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(40 * 30 * 10 + Math.PI * 25 * 4, 1);
});

test("R's ring sets the revolve angle, from the box beside it or typed", async ({ page, request }) => {
  // A rectangle off the Y axis, with no extrude, as the revolve test in viewer.spec.js has it.
  await resetDocument(request, {
    features: [{ id: "f1", type: "sketch", plane: "XY", polygon: [[10, 0], [20, 0], [20, 10], [10, 10]] }],
    rollback_index: 1,
  });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.timeline.length === 1);
  await settled(page);
  await page.locator('.timeline-chip[data-feature-id="f1"]').click();
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.sketch)).toBe("f1");
  await page.keyboard.press("r");
  await expect.poll(() => handle(page)).not.toBeNull();
  expect(await handle(page)).toMatchObject({ kind: "angular", value: 360, box: "360°" });
  await page.keyboard.type("90");
  await expect(page.locator("#modify-angle")).toHaveValue("90");
  expect((await handle(page)).value).toBe(90);
  await page.waitForFunction(() => window.__tcad.modifying.ok && window.__tcad.modifying.feature.angle === 90, null, SLOW);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.__tcad.timeline.length === 2, null, SLOW);
  await settled(page);
  // A quarter of the ring between radius 10 and 20, 10 high.
  expect((await rpc(request, "measure")).volume).toBeCloseTo((Math.PI * 300 * 10) / 4, 1);
});
