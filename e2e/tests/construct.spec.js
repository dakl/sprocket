// Construct features and the body tools: Offset plane, Axis, Point, Mirror,
// Linear and Circular pattern, and Combine, each driven from the toolbar.
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers");

const SLOW = { timeout: 15_000 };

// Two separate bodies: a 40 x 30 x 10 box (f2) and a 2 x 2 x 2 peg (f4) on top of it at x = 5.
const BOX_AND_PEG = {
  features: [
    { id: "f1", type: "sketch", plane: "XY", rect: { width: 40, height: 30 } },
    { id: "f2", type: "extrude", profile: "f1", distance: 10 },
    { id: "f3", type: "sketch", plane: { name: "XY", offset: 30 }, rect: { width: 2, height: 2, center: [5, 0] } },
    { id: "f4", type: "extrude", profile: "f3", distance: 2 },
  ],
  rollback_index: 4,
};

async function open(page, request, document) {
  await resetDocument(request, document);
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && (window.__tcad.mesh !== null || window.__tcad.timeline.length === 0));
  await settled(page);
}

const modifying = (page) => page.evaluate(() => window.__tcad.modifying);
const previewReady = (page) => page.waitForFunction(() => window.__tcad.modifying && window.__tcad.modifying.ok && window.__tcad.modifying.preview, null, SLOW);
const features = async (request) => (await rpc(request, "get_document")).features;
const bodies = async (request) => (await rpc(request, "get_bodies")).bodies;
const bodyRow = (page, name) => page.locator("#browser-tree .browser-label", { hasText: new RegExp(`^${name}$`) });

async function pickBody(page, name) {
  await bodyRow(page, name).click();
  await expect.poll(() => page.evaluate(() => window.__tcad.selectedBody)).not.toBeNull();
}

async function commit(page, count) {
  await page.keyboard.press("Enter");
  await page.waitForFunction((n) => window.__tcad.timeline.length === n, count, SLOW);
  await settled(page);
  await expect(page.locator("#modify-panel")).toBeHidden();
}

test("Offset plane adds a plane feature, draws it, and a sketch can start on it", async ({ page, request }, info) => {
  await open(page, request, { features: [], rollback_index: 0 });
  await expect(page.locator("#tool-offset-plane")).toBeEnabled();
  await page.locator("#tool-offset-plane").click();
  await expect(page.locator("#modify-panel")).toBeVisible();
  await page.locator("#modify-plane").selectOption("XZ");
  await page.locator("#modify-offset").fill("25");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.offset === 25 && window.__tcad.modifying.feature.plane === "XZ" && window.__tcad.modifying.ok, null, SLOW);
  await expect(page.locator("#modify-info")).toContainText("plane through");
  await page.screenshot({ path: info.outputPath("offset-plane-preview.png") });
  await commit(page, 1);

  const [plane] = await features(request);
  expect(plane).toMatchObject({ id: "f1", type: "plane", plane: "XZ", offset: 25 });
  await expect.poll(() => page.evaluate(() => window.__tcad.constructs.map((item) => item.kind))).toEqual(["plane"]);
  // XZ's normal is -Y in this engine: the plane sits 25 mm out along it.
  const drawn = (await page.evaluate(() => window.__tcad.constructs))[0].info;
  expect(Math.abs(drawn.origin[1])).toBeCloseTo(25, 6);
  await expect(page.locator("#browser-tree")).toContainText("f1 (plane)");
  await page.screenshot({ path: info.outputPath("offset-plane.png") });

  // A sketch on the plane feature: its button is in the plane panel.
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-construct="f1"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active);
  await cameraStill(page);
  await page.locator('#sketch-draw-panel [data-tool="rectangle"]').click();
  for (const uv of [[0, 0], [10, 6]]) {
    const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
    await page.mouse.click(point.x, point.y);
  }
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).entities.length).toBe(4);
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active && window.__tcad.timeline.length === 2);
  expect((await features(request))[1].plane).toEqual({ construct: "f1" });
  // The engine puts the sketch where the plane is.
  const landed = (await rpc(request, "resolve_plane", { plane: { construct: "f1" } })).plane;
  expect(Math.abs(landed.origin[1])).toBeCloseTo(25, 6);
});

test("Offset plane from a picked face starts on that face and follows it", async ({ page, request }) => {
  await open(page, request, { features: BOX_AND_PEG.features.slice(0, 2), rollback_index: 2 });
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(top);
  await page.locator("#tool-offset-plane").click();
  await expect(page.locator("#modify-plane")).toHaveValue("face");
  await page.locator("#modify-offset").fill("5");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.offset === 5 && window.__tcad.modifying.ok, null, SLOW);
  await commit(page, 3);
  const plane = (await features(request))[2];
  expect(plane.plane.feature).toBe("f2");
  expect(plane.plane.face.select).toBeDefined();
  expect((await page.evaluate(() => window.__tcad.constructs))[0].info.origin[2]).toBeCloseTo(15, 6);
});

test("Axis names a round face's axis, and a circular pattern can use it by name", async ({ page, request }) => {
  const post = [
    { id: "f1", type: "sketch", plane: "XY", circle: { radius: 6, center: [0, 0] } },
    { id: "f2", type: "extrude", profile: "f1", distance: 10 },
    { id: "f3", type: "sketch", plane: { name: "XY", offset: 10 }, circle: { radius: 1, center: [3, 0] } },
    { id: "f4", type: "extrude", profile: "f3", distance: 2 },
  ];
  await open(page, request, { features: post, rollback_index: 4 });
  await expect(page.locator("#tool-axis")).toBeDisabled();
  const { faces } = await rpc(request, "describe_geometry");
  // The round wall of the big post: the biggest cylinder.
  const wall = faces.map((face, index) => ({ face, index })).filter(({ face }) => face.kind === "CYLINDER").sort((a, b) => b.face.area - a.face.area)[0].index;
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), wall);
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(wall);
  await expect(page.locator("#tool-axis")).toBeEnabled();
  await page.locator("#tool-axis").click();
  await previewReady(page);
  await commit(page, 5);
  const axis = (await features(request))[4];
  expect(axis).toMatchObject({ id: "f5", type: "axis" });
  expect(axis.axis.base).toBe("f2");
  const drawn = (await page.evaluate(() => window.__tcad.constructs))[0].info;
  expect(drawn.origin.slice(0, 2)).toEqual([0, 0]);

  // Pattern the small pin about that axis by name: f4 is the pin's body and f2 the post.
  await pickBody(page, "f4");
  await page.locator("#tool-circular").click();
  await expect(page.locator("#modify-axis")).toBeVisible();
  await page.locator("#modify-axis").selectOption("f5");
  await page.locator("#modify-count").fill("4");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.circular && window.__tcad.modifying.feature.circular.count === 4 && window.__tcad.modifying.ok, null, SLOW);
  expect((await modifying(page)).feature.circular.axis).toEqual({ construct: "f5" });
  await commit(page, 6);
  const found = await bodies(request);
  expect(found).toHaveLength(2);
  expect(found[1].volume).toBeCloseTo(4 * Math.PI * 1 * 2, 3);
});

test("Point fills in a picked face's centre and adds a point feature", async ({ page, request }) => {
  await open(page, request, { features: BOX_AND_PEG.features.slice(0, 2), rollback_index: 2 });
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(top);
  await page.locator("#tool-point").click();
  await expect(page.locator("#modify-z")).toHaveValue("10");
  await page.locator("#modify-x").fill("4");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.at[0] === 4 && window.__tcad.modifying.ok, null, SLOW);
  await commit(page, 3);
  expect((await features(request))[2]).toMatchObject({ type: "point", at: [4, 0, 10] });
  await expect.poll(() => page.evaluate(() => window.__tcad.constructs.map((item) => item.kind))).toEqual(["point"]);
});

test("Mirror copies the picked body across a chosen plane", async ({ page, request }, info) => {
  await open(page, request, BOX_AND_PEG);
  await expect(page.locator("#tool-mirror")).toBeDisabled();
  await pickBody(page, "f4");
  await expect(page.locator("#tool-mirror")).toBeEnabled();
  await page.locator("#tool-mirror").click();
  await expect(page.locator("#modify-plane")).toBeVisible();
  await page.locator("#modify-plane").selectOption("YZ");
  await previewReady(page);
  await page.screenshot({ path: info.outputPath("mirror-preview.png") });
  expect((await modifying(page)).feature).toMatchObject({ type: "mirror", tool: "f4", base: "f4", plane: "YZ" });
  await commit(page, 5);
  const found = await bodies(request);
  expect(found.map((body) => body.id)).toEqual(["f2", "f4"]);
  // The peg and its mirror: two 2 x 2 x 2 pegs 10 mm apart, so two solids and 16 mm3.
  expect(found[1].volume).toBeCloseTo(16, 3);
});

test("Mirror can cut the copies out of another body", async ({ page, request }) => {
  await open(page, request, { features: [
    BOX_AND_PEG.features[0], BOX_AND_PEG.features[1],
    { id: "f3", type: "sketch", plane: "XY", rect: { width: 4, height: 4, center: [10, 0] } },
    { id: "f4", type: "extrude", profile: "f3", distance: 10 },
  ], rollback_index: 4 });
  await pickBody(page, "f4");
  await page.locator("#tool-mirror").click();
  await page.locator("#modify-plane").selectOption("YZ");
  await page.locator("#modify-into").selectOption({ label: "f2" });
  await page.locator("#modify-mode").selectOption("cut");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.mode === "cut" && window.__tcad.modifying.ok, null, SLOW);
  await commit(page, 5);
  const found = await bodies(request);
  // The box minus the 4 x 4 x 10 tool at x = 10 and its mirror at x = -10: 12000 - 2 * 160 (the tool is as tall as the box).
  expect(found.map((body) => body.id)).toEqual(["f2"]);
  expect(found[0].volume).toBeCloseTo(12000 - 320, 3);
});

test("Linear pattern repeats the picked body along a direction", async ({ page, request }) => {
  await open(page, request, BOX_AND_PEG);
  await pickBody(page, "f4");
  await page.locator("#tool-linear").click();
  await expect(page.locator("#modify-direction")).toBeVisible();
  await page.locator("#modify-direction").selectOption("Y");
  await page.locator("#modify-count").fill("3");
  await page.locator("#modify-spacing").fill("6");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.linear && window.__tcad.modifying.feature.linear.count === 3 && window.__tcad.modifying.feature.linear.spacing === 6 && window.__tcad.modifying.ok, null, SLOW);
  expect((await modifying(page)).feature).toMatchObject({ type: "pattern", tool: "f4", base: "f4", linear: { direction: "Y", count: 3, spacing: 6 } });
  await commit(page, 5);
  const peg = (await bodies(request))[1];
  expect(peg.volume).toBeCloseTo(3 * 8, 3);
  // A count the pattern cannot take is refused in the panel.
  await pickBody(page, "f4");
  await page.locator("#tool-linear").click();
  await page.locator("#modify-count").fill("1");
  await expect(page.locator("#modify-info")).toContainText("whole number, at least 2");
  await expect(page.locator("#modify-ok")).toBeDisabled();
  await page.keyboard.press("Escape");
});

test("Circular pattern turns the picked body about an axis", async ({ page, request }) => {
  await open(page, request, BOX_AND_PEG);
  await pickBody(page, "f4");
  await page.locator("#tool-circular").click();
  await page.locator("#modify-axis").selectOption("Z");
  await page.locator("#modify-count").fill("4");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.circular && window.__tcad.modifying.feature.circular.count === 4 && window.__tcad.modifying.ok, null, SLOW);
  await commit(page, 5);
  expect((await bodies(request))[1].volume).toBeCloseTo(4 * 8, 3);
  // A part turn is a separate field.
  await pickBody(page, "f2");
  await page.locator("#tool-circular").click();
  await expect(page.locator("#modify-angle")).toHaveValue("360");
  await page.keyboard.press("Escape");
});

test("Combine cuts one body from another", async ({ page, request }) => {
  const overlapping = [
    BOX_AND_PEG.features[0], BOX_AND_PEG.features[1],
    { id: "f3", type: "sketch", plane: "XY", rect: { width: 10, height: 10, center: [20, 0] } },
    { id: "f4", type: "extrude", profile: "f3", distance: 10 },
  ];
  await open(page, request, { features: overlapping, rollback_index: 4 });
  // The 10 x 10 x 10 tool sticks out of the box's +X side by 5 mm: 500 mm3 outside, 500 inside.
  await page.locator("#tool-combine").click();
  await expect(page.locator("#modify-target")).toBeVisible();
  await page.locator("#modify-target").selectOption({ label: "f2" });
  await page.locator("#modify-tool").selectOption({ label: "f4" });
  await page.locator("#modify-mode").selectOption("cut");
  await page.waitForFunction(() => window.__tcad.modifying.feature && window.__tcad.modifying.feature.operation === "subtract" && window.__tcad.modifying.ok, null, SLOW);
  await commit(page, 5);
  let found = await bodies(request);
  expect(found.map((body) => body.id)).toEqual(["f2"]);
  expect(found[0].volume).toBeCloseTo(12000 - 500, 3);
});

test("Combine refuses the same body twice, and is off with one body", async ({ page, request }) => {
  await open(page, request, { features: BOX_AND_PEG.features.slice(0, 2), rollback_index: 2 });
  await expect(page.locator("#tool-combine")).toBeDisabled();
  await open(page, request, BOX_AND_PEG);
  await page.locator("#tool-combine").click();
  await page.locator("#modify-tool").selectOption({ label: "f2" });
  await page.locator("#modify-target").selectOption({ label: "f2" });
  await expect(page.locator("#modify-info")).toContainText("different bodies");
  await expect(page.locator("#modify-ok")).toBeDisabled();
  await page.keyboard.press("Escape");
});
