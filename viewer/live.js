// Live updates: what the viewer shows when someone else moved the document,
// and which of the daemon's selection to put back after a reload.
//
// Pure functions with no DOM, so viewer/test/live.test.js runs them under node.
(function (root) {
  /// The cue for a `document_changed` notification, or null for none.
  /// `info` carries `author` ("viewer", "agent:<name>", "file") and `changes`
  /// ([{action, feature_id}]). The person's own edits and view changes, which
  /// have no author, get no cue: they already know.
  function changeCue(info, ownAuthor) {
    if (!info || !info.author || info.author === (ownAuthor || "viewer")) return null;
    const text = info.author === "file" ? "Reloaded from disk" : "Updated by " + info.author;
    const featureIds = (info.changes || []).map((change) => change.feature_id).filter(Boolean);
    return { text, featureIds: Array.from(new Set(featureIds)) };
  }

  /// The face and edge index to select from the daemon's selection view, or
  /// null when it does not belong to the part on screen. The viewer selects one
  /// face and one edge at most, so it takes the first of each.
  function selectionToApply(view, loadedRevision) {
    if (!view || view.stale || view.revision !== loadedRevision) return null;
    const first = (kind) => {
      const handle = (view.handles || []).find((item) => item[0] === kind);
      return handle === undefined ? null : Number(handle.slice(1));
    };
    return { face: first("f"), edge: first("e"), sketches: view.sketches || [] };
  }

  const api = { changeCue, selectionToApply };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewerLive = api;
})(typeof window !== "undefined" ? window : globalThis);
