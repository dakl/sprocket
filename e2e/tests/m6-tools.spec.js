// The M6 ops' buttons: Sweep, Loft, Draft, Split and Thread, each driven from
// the toolbar through the Modify panel, previewed, applied and checked against
// the engine. The documents are the golden cases' own where one fits.
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled } = require("./helpers.js");

const SLOW = { timeout: 15_000 };

const BOX = {
  features: [
    { id: "f1", type: "sketch", plane: "XY", rect: { width: 40, height: 30 } },
    { id: "f2", type: "extrude", profile: "f1", distance: 10 },
  ],
  rollback_index: 2,
};

async function open(page, request, document) {
  await resetDocument(request, document);
  await page.goto("/index.html");
  await page.waitForFunction(
    (count) => globalThis.__tcad && globalThis.__tcad.timeline.length === count,
    document.features.length,
  );
  await settled(page);
}

const modifying = (page) => page.evaluate(() => globalThis.__tcad.modifying);
const previewReady = (page) =>
  page.waitForFunction(
    () => globalThis.__tcad.modifying?.ok && globalThis.__tcad.modifying.preview,
    null,
    SLOW,
  );
const features = async (request) => (await rpc(request, "get_document")).features;
const chip = (page, id) => page.locator(`.timeline-chip[data-feature-id="${id}"]`);

async function commit(page, count) {
  await page.keyboard.press("Enter");
  await page.waitForFunction((n) => globalThis.__tcad.timeline.length === n, count, SLOW);
  await settled(page);
  await expect(page.locator("#modify-panel")).toBeHidden();
}

/// Clicks the first face on screen that `wanted` accepts, from describe_geometry's list.
async function clickFace(page, request, wanted) {
  const { faces } = await rpc(request, "describe_geometry");
  const indices = faces.filter(wanted).map((face) => Number(face.handle.slice(1)));
  const points = await page.evaluate(
    (list) => list.map((face) => ({ face, ...globalThis.__tcad.screenPointOf({ face }) })),
    indices,
  );
  const point = points.find((item) => item.visible);
  if (!point) {
    throw new Error("no wanted face is on screen");
  }
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => globalThis.__tcad.selection.face)).toBe(point.face);
  return point.face;
}

test("Sweep moves the selected sketch along the path sketch into a new body", async ({
  page,
  request,
}, info) => {
  await open(page, request, {
    features: [
      { id: "section", type: "sketch", plane: "YZ", rect: { width: 4, height: 4 } },
      {
        id: "route",
        type: "sketch",
        plane: "XY",
        entities: [
          { id: "e1", kind: "line", p0: [0, 0], p1: [20, 0] },
          { id: "e2", kind: "arc", c: [20, 10], r: 10, a0: -Math.PI / 2, a1: 0 },
        ],
        constraints: [],
      },
    ],
    rollback_index: 2,
  });
  await expect(page.locator("#tool-sweep")).toBeDisabled();
  await chip(page, "section").click();
  await expect(page.locator("#tool-sweep")).toBeEnabled();
  await page.locator("#tool-sweep").click();
  await previewReady(page);
  expect((await modifying(page)).feature).toEqual({
    type: "sweep",
    profile: "section",
    path: { sketch: "route" },
  });
  await page.screenshot({ path: info.outputPath("sweep-preview.png") });
  await commit(page, 3);
  // The golden case sweep_square_bend's volume.
  expect((await rpc(request, "measure")).volume).toBeCloseTo(571.327, 2);
  await page.keyboard.press("Meta+z");
  await page.waitForFunction(() => globalThis.__tcad.timeline.length === 2, null, SLOW);
});

test("Loft skins two sketches, ruled, with no selection needed", async ({
  page,
  request,
}, info) => {
  await open(page, request, {
    features: [
      { id: "foot", type: "sketch", plane: "XY", rect: { width: 20, height: 20 } },
      {
        id: "head",
        type: "sketch",
        plane: { name: "XY", offset: 10 },
        rect: { width: 10, height: 10 },
      },
    ],
    rollback_index: 2,
  });
  await expect(page.locator("#tool-loft")).toBeEnabled();
  await page.locator("#tool-loft").click();
  await expect(page.locator("#modify-panel")).toBeVisible();
  await expect(page.locator("#modify-first")).toHaveValue("foot");
  await expect(page.locator("#modify-second")).toHaveValue("head");
  await page.locator("#modify-ruled").check();
  await page.waitForFunction(
    () => globalThis.__tcad.modifying.feature?.ruled && globalThis.__tcad.modifying.ok,
    null,
    SLOW,
  );
  expect((await modifying(page)).feature).toEqual({
    type: "loft",
    sections: ["foot", "head"],
    ruled: true,
  });
  await page.screenshot({ path: info.outputPath("loft-preview.png") });
  await commit(page, 3);
  // The golden case loft_square_frustum: a frustum of 20 and 10 mm squares, 10 high.
  expect((await rpc(request, "measure")).volume).toBeCloseTo(2333.333, 2);
});

test("Draft tilts the picked side face about XY and refuses 45 degrees", async ({
  page,
  request,
}, info) => {
  await open(page, request, BOX);
  await expect(page.locator("#tool-draft")).toBeDisabled();
  await clickFace(
    page,
    request,
    (face) => face.kind === "PLANE" && face.normal && face.normal[2] === 0,
  );
  await page.locator("#tool-draft").click();
  await previewReady(page);
  const { feature } = await modifying(page);
  expect(feature).toMatchObject({ type: "draft", base: "f2", neutral: "XY", angle: 3 });
  expect(feature.faces.expected_count).toBe(1);
  await page.locator("#modify-angle").fill("45");
  await expect(page.locator("#modify-info")).toContainText("-45 and 45");
  await page.locator("#modify-angle").fill("5");
  await page.waitForFunction(
    () =>
      globalThis.__tcad.modifying.feature &&
      globalThis.__tcad.modifying.feature.angle === 5 &&
      globalThis.__tcad.modifying.ok,
    null,
    SLOW,
  );
  await page.screenshot({ path: info.outputPath("draft-preview.png") });
  await commit(page, 3);
  expect((await features(request))[2]).toMatchObject({ type: "draft", angle: 5 });
  // Leaning one wall in takes material away.
  expect((await rpc(request, "measure")).volume).toBeLessThan(40 * 30 * 10);
});

test("Split cuts the only body along XZ and keeps both pieces", async ({ page, request }, info) => {
  await open(page, request, BOX);
  await expect(page.locator("#tool-split")).toBeEnabled();
  await page.locator("#tool-split").click();
  await expect(page.locator("#modify-panel")).toBeVisible();
  await page.locator("#modify-plane").selectOption("XZ");
  await page.waitForFunction(
    () =>
      globalThis.__tcad.modifying.feature &&
      globalThis.__tcad.modifying.feature.plane === "XZ" &&
      globalThis.__tcad.modifying.ok,
    null,
    SLOW,
  );
  expect((await modifying(page)).feature).toEqual({ type: "split", base: "f2", plane: "XZ" });
  await page.screenshot({ path: info.outputPath("split-preview.png") });
  await commit(page, 3);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(40 * 30 * 10, 3);
  expect((await rpc(request, "describe_geometry")).counts.solids).toBe(2);
});

test("Thread sizes itself from the picked shaft and applies a cosmetic thread", async ({
  page,
  request,
}, info) => {
  // A 6 mm shaft standing on a plate.
  await open(page, request, {
    features: [
      ...BOX.features,
      { id: "f3", type: "sketch", plane: { name: "XY", offset: 10 }, circle: { radius: 3 } },
      { id: "f4", type: "extrude", profile: "f3", base: "f2", distance: 12 },
    ],
    rollback_index: 4,
  });
  await clickFace(page, request, (face) => face.kind === "CYLINDER");
  await page.locator("#tool-thread").click();
  await previewReady(page);
  const { feature } = await modifying(page);
  expect(feature).toMatchObject({ type: "thread", base: "f4", designation: "M6" });
  expect(feature.face.expected_count).toBe(1);
  await page.screenshot({ path: info.outputPath("thread-preview.png") });
  await commit(page, 5);
  expect((await features(request))[4]).toMatchObject({ type: "thread", designation: "M6" });
});

test("Thread on a flat face says to pick a round one", async ({ page, request }) => {
  await open(page, request, BOX);
  await clickFace(page, request, (face) => face.kind === "PLANE");
  await page.locator("#tool-thread").click();
  await expect(page.locator("#modify-panel")).toBeHidden();
  await expect(page.locator("body")).toContainText("Pick a round face");
});
