// Opening and saving documents. The Swift app's menus call these same methods,
// so what is checked here is what the menus do: the viewer follows the engine
// to another document, and a file is only written when it should be.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test, expect } = require("@playwright/test");
const fixture = require("../fixtures/box.json");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers");

const CYLINDER = {
  features: [
    { id: "s1", type: "sketch", plane: "XY", circle: { radius: 5 } },
    { id: "e1", type: "extrude", profile: "s1", distance: 10 },
  ],
  rollback_index: 2,
};

let original;
let folder;

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, fixture);
  original = (await rpc(request, "get_document")).path;
  folder = fs.mkdtempSync(path.join(os.tmpdir(), "sprocket-files-"));
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);
});

// The server outlives the tests, so each one hands the shared document back.
test.afterEach(async ({ request }) => {
  await rpc(request, "open_document", { path: original });
});

const writeDocument = (name, document) => {
  const file = path.join(folder, name);
  fs.writeFileSync(file, JSON.stringify(document));
  return file;
};

test("opening a document replaces the part and the timeline, even at the same revision", async ({ page, request }) => {
  const file = writeDocument("cylinder.json", CYLINDER);
  await rpc(request, "open_document", { path: file });
  await page.waitForFunction(() => window.__tcad.timeline.some((entry) => entry.id === "e1"));
  await settled(page);
  expect(await page.evaluate(() => window.__tcad.timeline.map((entry) => entry.id))).toEqual(["s1", "e1"]);
  // 3 faces is the cylinder, and the box had 6 at the same revision, 0.
  await page.waitForFunction(() => window.__tcad.mesh && window.__tcad.mesh.faces === 3);
});

test("opening a document drops a sketch in progress, which belonged to the old one", async ({ page, request }) => {
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active);
  await cameraStill(page);

  await rpc(request, "open_document", { path: writeDocument("cylinder.json", CYLINDER) });
  await page.waitForFunction(() => !window.__tcad.sketch.active);
  await settled(page);
});

test("opening a file leaves it as it was, and a bad file changes nothing", async ({ page, request }) => {
  const file = writeDocument("cylinder.json", CYLINDER);
  const before = fs.readFileSync(file, "utf8");
  await rpc(request, "open_document", { path: file });
  expect(fs.readFileSync(file, "utf8")).toBe(before);

  const broken = path.join(folder, "broken.json");
  fs.writeFileSync(broken, "{ not json");
  await expect(rpc(request, "open_document", { path: broken })).rejects.toThrow(/not valid JSON/);
  expect((await rpc(request, "get_document")).path).toBe(file);
});

test("save as writes a copy, and later edits go to the copy and not the first file", async ({ request }) => {
  const copy = path.join(folder, "copy.json");
  await rpc(request, "save_as", { path: copy });
  expect(JSON.parse(fs.readFileSync(copy, "utf8")).features).toHaveLength(2);

  const firstFileBefore = fs.readFileSync(original, "utf8");
  await rpc(request, "set_parameter", { name: "width", value: 55 });
  expect(JSON.parse(fs.readFileSync(copy, "utf8")).parameters).toEqual({ width: 55 });
  expect(fs.readFileSync(original, "utf8")).toBe(firstFileBefore);
  await rpc(request, "set_parameter", { name: "width", value: null });
});

test("a new document is empty, and the viewer shows nothing", async ({ page, request }) => {
  await rpc(request, "new_document", { path: path.join(folder, "fresh.json") });
  await page.waitForFunction(() => window.__tcad.timeline.length === 0);
  await settled(page);
  expect(await page.evaluate(() => window.__tcad.mesh)).toBeNull();
});
