// The agent's pointer and screenshot: an agent on the daemon clicks, types and
// presses keys in this viewer through agent_pointer, and screenshot_app gives
// it a picture. In a browser the picture is the canvas only; the app's
// whole-window capture is Swift's and is checked by hand (see SPEC).
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled } = require("./helpers");

const SLOW = { timeout: 15_000 };

const BOX = {
  features: [
    { id: "f1", type: "sketch", plane: "XY", rect: { width: 40, height: 30 } },
    { id: "f2", type: "extrude", profile: "f1", distance: 10 },
  ],
  rollback_index: 2,
};

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, BOX);
  await page.goto("/index.html");
  await page.waitForFunction(() => globalThis.__tcad && globalThis.__tcad.timeline.length === 2);
  await settled(page);
});

test("a click on a toolbar button opens its tool, and the pointer shows where it clicked", async ({ page, request }, info) => {
  const answer = await rpc(request, "agent_pointer", { action: "click", target: "#tool-shell" });
  expect(answer.element).toMatch(/^button#tool-shell/);
  await expect(page.locator("#modify-panel")).toBeVisible();
  await expect(page.locator("#agent-pointer")).toHaveClass(/shown/);
  const box = await page.locator("#tool-shell").boundingBox();
  // The pointer slides there over 0.3 s, which a loaded machine can stretch.
  await expect.poll(async () => Math.abs((await page.locator("#agent-pointer").boundingBox()).x - (box.x + box.width / 2)), SLOW).toBeLessThan(2);
  await page.screenshot({ path: info.outputPath("agent-pointer.png") });
  // Enter applies the shell, as a person's key press would.
  await rpc(request, "agent_pointer", { action: "key", key: "Enter" });
  await page.waitForFunction(() => globalThis.__tcad.timeline.length === 3, null, SLOW);
});

test("a click on a face of the part selects it", async ({ page, request }) => {
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => globalThis.__tcad.screenPointOf({ face }), top);
  const answer = await rpc(request, "agent_pointer", { action: "click", x: point.x, y: point.y });
  expect(answer.element).toBe("canvas");
  await expect.poll(() => page.evaluate(() => globalThis.__tcad.selection.face)).toBe(top);
});

test("typing goes into the focused field with input events", async ({ page, request }) => {
  await rpc(request, "agent_pointer", { action: "click", target: "#tool-shell" });
  await expect(page.locator("#modify-thickness")).toBeVisible();
  await rpc(request, "agent_pointer", { action: "click", target: "#modify-thickness" });
  await page.locator("#modify-thickness").fill("");
  await rpc(request, "agent_pointer", { action: "type", text: "1.5" });
  await page.waitForFunction(() => globalThis.__tcad.modifying.feature && globalThis.__tcad.modifying.feature.thickness === 1.5, null, SLOW);
});

test("a selector that matches nothing comes back as the call's error", async ({ request }) => {
  const response = await request.post("/rpc", { data: { jsonrpc: "2.0", id: 1, method: "agent_pointer", params: { action: "click", target: "#no-such" } } });
  expect((await response.json()).error.message).toContain("no element matches #no-such");
});

test("screenshot_app in a browser gives the canvas as a PNG", async ({ request }, info) => {
  const path = info.outputPath("canvas.png");
  const result = await rpc(request, "screenshot_app", { path });
  expect(result.scope).toBe("canvas");
  expect(result.width).toBeGreaterThan(100);
  const bytes = require("node:fs").readFileSync(path);
  expect(bytes.subarray(1, 4).toString()).toBe("PNG");
});
