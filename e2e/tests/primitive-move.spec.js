// Primitive and Move from the toolbar: each opens the Modify panel, previews,
// applies on Enter, and is checked against the engine's measure.
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
  await page.waitForFunction((count) => globalThis.__tcad && globalThis.__tcad.timeline.length === count, document.features.length);
  await settled(page);
}

const modifying = (page) => page.evaluate(() => globalThis.__tcad.modifying);
const ready = (page, check) =>
  page.waitForFunction((source) => {
    const tool = globalThis.__tcad.modifying;
    // biome-ignore lint/security/noGlobalEval: the check is test code passed as text
    return tool?.ok && tool.feature && (0, eval)(source)(tool.feature);
  }, check.toString(), SLOW);

async function commit(page, count) {
  await page.keyboard.press("Enter");
  await page.waitForFunction((n) => globalThis.__tcad.timeline.length === n, count, SLOW);
  await settled(page);
  await expect(page.locator("#modify-panel")).toBeHidden();
}

test("Primitive makes a cylinder in an empty document, then cuts a box out of it", async ({ page, request }, info) => {
  await open(page, request, { features: [], rollback_index: 0 });
  await expect(page.locator("#tool-primitive")).toBeEnabled();
  await page.locator("#tool-primitive").click();
  await expect(page.locator("#modify-panel")).toBeVisible();
  await expect(page.locator("#modify-width")).toBeVisible();
  await page.locator("#modify-shape").selectOption("cylinder");
  await expect(page.locator("#modify-width")).toBeHidden();
  await page.locator("#modify-radius").fill("5");
  await page.locator("#modify-height").fill("12");
  await ready(page, (feature) => feature.shape === "cylinder" && feature.radius === 5 && feature.height === 12);
  expect((await modifying(page)).feature).toEqual({ type: "primitive", shape: "cylinder", radius: 5, height: 12 });
  await page.screenshot({ path: info.outputPath("primitive-cylinder.png") });
  await commit(page, 1);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(Math.PI * 25 * 12, 2);

  await page.locator("#tool-primitive").click();
  await page.locator("#modify-width").fill("4");
  await page.locator("#modify-depth").fill("20");
  await page.locator("#modify-height").fill("20");
  const into = page.locator("#modify-into");
  await into.selectOption({ index: 1 });
  await page.locator("#modify-mode").selectOption("cut");
  await ready(page, (feature) => feature.mode === "cut" && feature.width === 4);
  await commit(page, 2);
  // A 4 mm wide slab through the middle of the 10 mm cylinder takes 4 x 10 x 12... minus the round ends it misses.
  const slab = 2 * (2 * Math.sqrt(25 - 4) + 25 * Math.asin(2 / 5)) * 12;
  expect((await rpc(request, "measure")).volume).toBeCloseTo(Math.PI * 25 * 12 - slab, 1);
});

test("Move shifts the picked body, and with Copy on leaves it and adds a turned copy", async ({ page, request }, info) => {
  await open(page, request, BOX);
  await expect(page.locator("#tool-move")).toBeEnabled();
  await page.locator("#tool-move").click();
  await expect(page.locator("#modify-panel")).toBeVisible();
  await expect(page.locator("#modify-info")).toContainText("distance or an angle");
  await page.locator("#modify-dz").fill("5");
  await ready(page, (feature) => feature.translate && feature.translate[2] === 5);
  expect((await modifying(page)).feature).toEqual({ type: "move", base: "f2", translate: [0, 0, 5] });
  await commit(page, 3);
  const moved = await rpc(request, "measure");
  expect(moved.bounding_box.min[2]).toBeCloseTo(5, 4);

  await page.locator("#tool-move").click();
  await page.locator("#modify-copy").check();
  await page.locator("#modify-dx").fill("50");
  await page.locator("#modify-angle").fill("90");
  await ready(page, (feature) => feature.tool && feature.rotate && feature.translate);
  await page.screenshot({ path: info.outputPath("move-copy.png") });
  await commit(page, 4);
  expect((await rpc(request, "describe_geometry")).counts.solids).toBe(2);
  expect((await rpc(request, "measure")).volume).toBeCloseTo(2 * 40 * 30 * 10, 2);
});

test("an agent opens Primitive and applies it through agent_pointer", async ({ page, request }) => {
  await open(page, request, { features: [], rollback_index: 0 });
  await rpc(request, "agent_pointer", { action: "click", target: "#tool-primitive" });
  await expect(page.locator("#modify-panel")).toBeVisible();
  await page.waitForFunction(() => globalThis.__tcad.modifying?.ok, null, SLOW);
  await rpc(request, "agent_pointer", { action: "key", key: "Enter" });
  await page.waitForFunction(() => globalThis.__tcad.timeline.length === 1, null, SLOW);
  // The default box is 20 mm a side.
  expect((await rpc(request, "measure")).volume).toBeCloseTo(8000, 2);
});
