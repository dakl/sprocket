// Floating labels (L): a name chip for every shown body and sketch, with a
// leader line to it. Assertions read `window.__tcad.labels`.
const { test, expect } = require("@playwright/test");
const fixture = require("../fixtures/box.json");

const { rpc, resetDocument, settled } = require("./helpers");

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, fixture);
  // Visibility is view state, which resetDocument leaves alone.
  await rpc(request, "set_visibility", { id: "f2", visible: true });
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);
});

const labels = (page) => page.evaluate(() => window.__tcad.labels.map((label) => [label.kind, label.id]).sort());

test("L shows a label for the body and the sketch, each with a leader line, and L again hides them", async ({ page }) => {
  expect(await labels(page)).toEqual([]);
  await page.keyboard.press("l");
  await page.waitForFunction(() => window.__tcad.labels.length === 2);
  expect(await labels(page)).toEqual([
    ["body", "f2"],
    ["sketch", "f1"],
  ]);
  await expect(page.locator(".floating-label")).toHaveCount(2);
  await expect(page.locator("#floating-leaders line")).toHaveCount(2);
  await expect(page.locator("#tool-labels")).toHaveClass(/active/);

  // Each label sits apart from its anchor, inside the window.
  for (const label of await page.evaluate(() => window.__tcad.labels)) {
    expect(Math.hypot(label.x + label.width / 2 - label.anchorX, label.y + label.height / 2 - label.anchorY)).toBeGreaterThan(20);
    expect(label.x).toBeGreaterThanOrEqual(0);
    expect(label.x + label.width).toBeLessThanOrEqual(1280);
  }

  await page.keyboard.press("l");
  expect(await labels(page)).toEqual([]);
  await expect(page.locator(".floating-label")).toHaveCount(0);
  await expect(page.locator("#floating-leaders line")).toHaveCount(0);
});

test("a hidden body gets no label", async ({ page, request }) => {
  await page.locator("#tool-labels").click();
  await page.waitForFunction(() => window.__tcad.labels.length === 2);

  await rpc(request, "set_visibility", { id: "f2", visible: false });
  await page.waitForFunction(() => window.__tcad.bodies[0].visible === false);
  await page.waitForFunction(() => window.__tcad.labels.length === 1);
  expect(await labels(page)).toEqual([["sketch", "f1"]]);

  await rpc(request, "set_visibility", { id: "f2", visible: true });
  await page.waitForFunction(() => window.__tcad.labels.length === 2);
});

test("a click on a label selects what it names", async ({ page }) => {
  await page.keyboard.press("l");
  await page.waitForFunction(() => window.__tcad.labels.length === 2);

  await page.locator('.floating-label[data-key="body:f2"]').click();
  expect(await page.evaluate(() => window.__tcad.selectedBody)).toBe("f2");
  await expect(page.locator('.floating-label[data-key="body:f2"]')).toHaveClass(/selected/);

  await page.locator('.floating-label[data-key="sketch:f1"]').click();
  expect(await page.evaluate(() => window.__tcad.selection.sketch)).toBe("f1");
});
