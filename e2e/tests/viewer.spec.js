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

// --- M4.5: selection as conversation -----------------------------------------

test("a face picked in the viewer is the selection an agent reads, with a sentence for it", async ({ page, request }) => {
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(async () => (await rpc(request, "get_selection")).handles).toEqual(["f" + top]);
  const selection = await rpc(request, "get_selection");
  expect(selection.stale).toBe(false);
  expect(selection.descriptions[0].description).toContain("normal +Z");
  // Escape clears it, and the agent sees that too.
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await rpc(request, "get_selection")).handles).toEqual([]);
});

test("a highlight from an agent shows in the viewer and does not touch the selection", async ({ page, request }) => {
  await rpc(request, "highlight", { handles: ["f0", "e0"] });
  await expect.poll(() => page.evaluate(() => window.__tcad.highlighted)).toEqual({ handles: ["f0", "e0"], sketches: [] });
  expect(await page.evaluate(() => window.__tcad.selection)).toMatchObject({ face: null, edge: null });
  await rpc(request, "highlight", { handles: [] });
  await expect.poll(() => page.evaluate(() => window.__tcad.highlighted.handles)).toEqual([]);
});

// --- M4: a picked face becomes a selector, not a position ----------------------

test("push/pull saves the picked face as a selector, and the feature survives a change of dimension", async ({ page, request }) => {
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect(page.locator("#tool-extrude")).toBeEnabled();
  await page.locator("#tool-extrude").click();
  await expect(page.locator("#extrude-panel")).toBeVisible();
  await page.keyboard.type("5");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !window.__tcad.extruding);
  await settled(page);

  const features = (await rpc(request, "get_document")).features;
  const pushed = features[features.length - 1];
  expect(pushed.type).toBe("extrude_face");
  expect(pushed.face.expected_count).toBe(1);
  expect(JSON.stringify(pushed.face.select)).toContain("new_since");

  // Widen the sketch. A selector made from a click position could land on another face.
  const polygon = [[-30, -15], [30, -15], [30, 15], [-30, 15]];
  const wider = await rpc(request, "apply_edit", { edits: [{ op: "update", id: "f1", feature: { id: "f1", type: "sketch", plane: "XY", polygon } }] });
  expect(wider.built.ok).toBe(true);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(60 * 30 * 15, 3);
});

// --- Live updates: another client or the file on disk moves the open page ------

const fs = require("fs");
const path = require("path");

const TOP = { select: [{ op: "faces" }, { op: "at_end", axis: "Z", end: "max" }], expected_count: 1 };

test("a second client's edits update the open page live, keeping camera and selection", async ({ page, request }) => {
  await page.evaluate(() => { window.__sameLoad = true; });
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(async () => (await rpc(request, "get_selection")).handles).toEqual(["f" + top]);
  const camera = await page.evaluate(() => window.__tcad.camera);

  // Face colour.
  await rpc(request, "set_appearances", { appearances: [{ color: "#2f9e44", select: TOP }], author: "agent:test" });
  await page.waitForFunction((face) => window.__tcad.faceColour(face) === "#2f9e44", top);
  await expect.poll(() => page.evaluate(() => window.__tcad.status)).toBe("Updated by agent:test");
  // The top face did not move, so it is still selected, here and for an agent.
  await settled(page);
  expect((await page.evaluate(() => window.__tcad.selection)).face).toBe(top);
  expect((await rpc(request, "get_selection")).stale).toBe(false);

  // Geometry, and the changed row glows.
  await rpc(request, "apply_edit", { edits: [{ op: "update", id: "f2", feature: { ...fixture.features[1], distance: 7 } }], author: "agent:test" });
  await page.waitForFunction(() => window.__tcad.features[1].distance === 7);
  await expect(chip(page, "f2")).toHaveClass(/timeline-chip-changed/);

  // Body colour and visibility, in the bodies list.
  await rpc(request, "set_body", { id: "f2", color: "#123456", author: "agent:test" });
  await page.waitForFunction(() => window.__tcad.bodies[0] && window.__tcad.bodies[0].color === "#123456");
  await expect(page.locator(".browser-swatch").first()).toHaveValue("#123456");
  await rpc(request, "set_visibility", { id: "f2", visible: false });
  await page.waitForFunction(() => window.__tcad.bodies[0].visible === false);
  await rpc(request, "set_visibility", { id: "f2", visible: true });
  await page.waitForFunction(() => window.__tcad.bodies[0].visible !== false);

  // A parameter change.
  await rpc(request, "set_parameter", { name: "h", value: 9, author: "agent:test" });
  const { revision } = await rpc(request, "get_document");
  await page.waitForFunction((expected) => window.__tcad.revision === expected, revision);

  // All of it without a page load, and the camera never moved.
  await settled(page);
  expect(await page.evaluate(() => window.__sameLoad)).toBe(true);
  const after = await page.evaluate(() => window.__tcad.camera);
  [...after.position, ...after.target].forEach((value, k) => expect(value).toBeCloseTo([...camera.position, ...camera.target][k], 6));
  await rpc(request, "set_appearances", { appearances: [] });
  await rpc(request, "set_body", { id: "f2", color: null });
});

/// What `sprocket build`, git or a generator script does to the open file.
function rewriteOnDisk(file, change) {
  const document = JSON.parse(fs.readFileSync(file, "utf8"));
  change(document);
  const temporary = path.join(path.dirname(file), "rewrite.tmp");
  fs.writeFileSync(temporary, JSON.stringify(document));
  fs.renameSync(temporary, file);
}

test("the document rewritten on disk reloads in the open page", async ({ page, request }) => {
  await page.evaluate(() => { window.__sameLoad = true; });
  const { path: file } = await rpc(request, "get_document");
  rewriteOnDisk(file, (document) => { document.features[1].distance = 13; });
  await page.waitForFunction(() => window.__tcad.features[1].distance === 13, null, { timeout: 3000 });
  await expect.poll(() => page.evaluate(() => window.__tcad.status)).toBe("Reloaded from disk");
  expect(await page.evaluate(() => window.__sameLoad)).toBe(true);
});

test("an edit that races a rewrite on disk is a conflict the person settles", async ({ page, request }) => {
  const { path: file } = await rpc(request, "get_document");
  rewriteOnDisk(file, (document) => { document.features[1].distance = 21; });
  // Before the watcher has looked: the engine must not save over the file.
  await rpc(request, "apply_edit", { edits: [{ op: "update", id: "f2", feature: { ...fixture.features[1], distance: 4 } }], author: "viewer" });
  await expect(page.locator("#conflict")).toBeVisible();
  expect(JSON.parse(fs.readFileSync(file, "utf8")).features[1].distance).toBe(21);
  await page.locator("#conflict-file").click();
  await page.waitForFunction(() => window.__tcad.features[1].distance === 21);
  await expect(page.locator("#conflict")).toBeHidden();
  expect((await rpc(request, "get_file_status")).conflict).toBe(null);
});

// --- Colour picker and right-click menu ----------------------------------------

test("right-click a face, pick a colour: the face is coloured in the document and on screen", async ({ page, request }) => {
  await rpc(request, "set_appearances", { appearances: [] });
  await settled(page);
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);

  // A right-drag pans and opens nothing.
  await page.mouse.move(point.x, point.y);
  await page.mouse.down({ button: "right" });
  await page.mouse.move(point.x + 40, point.y + 30, { steps: 5 });
  await page.mouse.up({ button: "right" });
  expect(await page.evaluate(() => window.__tcad.contextMenu)).toBe(null);
  await settled(page);

  const again = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(again.x, again.y, { button: "right" });
  await expect.poll(() => page.evaluate(() => window.__tcad.contextMenu)).toEqual(["Colour…", "Body colour…", "Hide body"]);
  await page.getByRole("menuitem", { name: "Colour…", exact: true }).click();
  await expect(page.locator("#colour-popover")).toBeVisible();
  await page.locator('#colour-popover .palette .swatch[data-hex="#2f9e44"]').click();

  await expect.poll(async () => (await rpc(request, "get_document")).appearances.map((entry) => entry.color)).toEqual(["#2f9e44"]);
  await page.waitForFunction((face) => window.__tcad.faceColour(face) === "#2f9e44", top);
  // Only that face: the box the engine gave picks it out alone.
  const coloured = await page.evaluate((count) => Array.from({ length: count }, (_, k) => window.__tcad.faceColour(k)), faces.length);
  expect(coloured.filter((hex) => hex === "#2f9e44")).toHaveLength(1);

  // The hex box, and the recent list remembers the green.
  const point2 = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point2.x, point2.y, { button: "right" });
  await expect.poll(() => page.evaluate(() => window.__tcad.contextMenu)).toContain("Clear face colour");
  await page.getByRole("menuitem", { name: "Colour…", exact: true }).click();
  await expect(page.locator('#colour-popover .recent .swatch[data-hex="#2f9e44"]')).toHaveCount(1);
  await page.locator("#colour-hex").fill("#d4af37");
  await page.locator("#colour-hex").press("Enter");
  await expect.poll(async () => (await rpc(request, "get_document")).appearances.map((entry) => entry.color)).toEqual(["#d4af37"]);

  // Undo puts the green back: it is an ordinary edit.
  await page.mouse.click(640, 790);
  await page.keyboard.press("Meta+z");
  await expect.poll(async () => (await rpc(request, "get_document")).appearances.map((entry) => entry.color)).toEqual(["#2f9e44"]);

  // Clear.
  await settled(page);
  const point3 = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point3.x, point3.y, { button: "right" });
  await page.getByRole("menuitem", { name: "Clear face colour" }).click();
  await expect.poll(async () => (await rpc(request, "get_document")).appearances).toEqual([]);
});

test("a body row's right-click menu colours and hides the body; the swatch still works", async ({ page, request }) => {
  const row = page.locator(".browser-row").filter({ has: page.locator(".browser-swatch") }).first();
  await row.click({ button: "right" });
  await expect.poll(() => page.evaluate(() => window.__tcad.contextMenu)).toEqual(["Colour…", "Hide"]);
  await page.getByRole("menuitem", { name: "Colour…", exact: true }).click();
  await page.locator('#colour-popover .palette .swatch[data-hex="#d83a2e"]').click();
  await expect.poll(async () => (await rpc(request, "get_bodies")).bodies[0].color).toBe("#d83a2e");

  await page.locator(".browser-row").filter({ has: page.locator(".browser-swatch") }).first().click({ button: "right" });
  await page.getByRole("menuitem", { name: "Hide" }).click();
  await expect.poll(async () => (await rpc(request, "get_bodies")).bodies[0].visible).toBe(false);
  await rpc(request, "set_visibility", { id: "f2", visible: true });

  // The swatch in the row still sends set_body by itself.
  await page.waitForFunction(() => window.__tcad.bodies[0].visible !== false);
  await page.locator(".browser-swatch").first().evaluate((input) => {
    input.value = "#1f6b3a";
    input.dispatchEvent(new Event("change"));
  });
  await expect.poll(async () => (await rpc(request, "get_bodies")).bodies[0].color).toBe("#1f6b3a");
  await rpc(request, "set_body", { id: "f2", color: null });
});

// --- Material presets ------------------------------------------------------------

test("a finish picked for a face and for a body reaches the document, the mesh and the materials drawn", async ({ page, request }) => {
  await rpc(request, "set_appearances", { appearances: [] });
  await settled(page);
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y, { button: "right" });
  await expect.poll(() => page.evaluate(() => window.__tcad.contextMenu)).toContain("Colour…");
  await page.getByRole("menuitem", { name: "Colour…", exact: true }).click();
  await page.locator('#colour-popover .finishes button[data-material="bare_metal"]').click();

  await expect.poll(async () => (await rpc(request, "get_document")).appearances.map((entry) => entry.material)).toEqual(["bare_metal"]);
  await page.waitForFunction((face) => window.__tcad.faceFinish(face) !== null, top);
  const finish = await page.evaluate((face) => window.__tcad.faceFinish(face), top);
  expect(finish[0]).toBeCloseTo(0.2, 5);
  expect(finish[1]).toBeCloseTo(1, 5);
  expect(await page.evaluate(() => window.__tcad.finishMaterials)).toEqual([{ roughness: expect.any(Number), metalness: 1, hasEnvironment: true }]);

  // The body: anodised aluminium from the tree row's menu.
  await page.locator(".browser-row").filter({ has: page.locator(".browser-swatch") }).first().click({ button: "right" });
  await page.getByRole("menuitem", { name: "Colour…", exact: true }).click();
  await page.locator('#colour-popover .finishes button[data-material="anodised_aluminium"]').click();
  await expect.poll(async () => (await rpc(request, "get_bodies")).bodies[0].material).toBe("anodised_aluminium");
  await page.waitForFunction(() => window.__tcad.finishMaterials.length === 2);

  await rpc(request, "set_appearances", { appearances: [] });
  await rpc(request, "set_body", { id: "f2", material: null });
});

test("a referenced file rewritten on disk rebuilds the open document live", async ({ page, request }) => {
  const { path: file } = await rpc(request, "get_document");
  const boardPath = path.join(path.dirname(file), "board.sprocket");
  const board = (extra) => ({
    features: [
      { id: "s", type: "sketch", plane: { name: "XY", offset: 20 }, rect: { width: 10, height: 10 } },
      { id: "b", type: "extrude", profile: "s", distance: 2 },
      ...extra,
    ],
  });
  fs.writeFileSync(boardPath, JSON.stringify(board([])));
  await resetDocument(request, { features: [...fixture.features, { id: "ref", type: "reference", file: "board.sprocket" }], rollback_index: 3 });
  await page.evaluate(() => { window.__sameLoad = true; });
  await page.waitForFunction(() => window.__tcad.bodies.some((body) => body.reference && body.faces === 6));
  const revision = (await rpc(request, "get_document")).revision;

  rewriteOnDisk(boardPath, (document) => {
    document.features.push(
      { id: "cs", type: "sketch", plane: { name: "XY", offset: 22 }, circle: { radius: 2 } },
      { id: "c", type: "extrude", profile: "cs", base: "b", mode: "join", distance: 3 }
    );
  });
  await page.waitForFunction(() => window.__tcad.bodies.some((body) => body.reference && body.faces === 8), null, { timeout: 4000 });
  await expect.poll(() => page.evaluate(() => window.__tcad.status)).toBe("Rebuilt: board.sprocket changed");
  expect((await rpc(request, "get_document")).revision).toBe(revision);
  expect(await page.evaluate(() => window.__sameLoad)).toBe(true);
});

test("a cosmetic thread is drawn as a helix over its hole, one turn per pitch", async ({ page, request }) => {
  const threaded = require("../../engine/tests/golden/thread_cosmetic_m3/document.json");
  await resetDocument(request, { features: threaded.features, rollback_index: threaded.features.length });
  await settled(page);
  // 4 mm of M3 at 0.5 mm pitch is 8 turns of 24 steps: 193 points.
  await expect.poll(() => page.evaluate(() => window.__tcad.threads)).toEqual([{ designation: "M3", points: 193 }]);
  await rpc(request, "set_rollback", { index: threaded.features.length - 1 });
  await expect.poll(() => page.evaluate(() => window.__tcad.threads)).toEqual([]);
});
