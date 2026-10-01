// Shared by the Playwright specs: talking to the engine directly, and waiting
// for the viewer to settle.
async function rpc(request, method, params = {}) {
  const response = await request.post("/rpc", { data: { jsonrpc: "2.0", id: 1, method, params } });
  const body = await response.json();
  if (body.error) throw new Error(body.error.message);
  return body.result;
}

/// Puts the shared document to `document` ({features, rollback_index}), through
/// the same edit API an agent uses. The server outlives the tests, so each one
/// starts from here.
async function resetDocument(request, document) {
  const current = await rpc(request, "get_document");
  const edits = [
    ...current.features.map((feature) => ({ op: "delete", id: feature.id })).reverse(),
    ...document.features.map((feature) => ({ op: "insert", feature })),
    { op: "set_rollback", index: document.rollback_index },
  ];
  await rpc(request, "apply_edit", { edits });
  for (const name of Object.keys(current.parameters)) {
    await rpc(request, "set_parameter", { name, value: null });
  }
}

async function settled(page) {
  await page.waitForFunction(() => window.__tcad && window.__tcad.idle);
}

/// Waits for the camera to stop easing into a sketch plane. A click made while
/// it moves lands somewhere the test did not aim. The viewer says when it is
/// still, so this does not guess from two readings that a slow machine can
/// make equal before the move has begun.
async function cameraStill(page) {
  await page.waitForFunction(() => !window.__tcad.cameraMoving);
}

module.exports = { rpc, resetDocument, settled, cameraStill };
