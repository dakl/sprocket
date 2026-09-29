// Turns what the viewer does into document edits: the one place these rules live.
//
// The viewer speaks in gestures ("extrude this face", "drag this sketch
// corner", "roll back to here"). The daemon speaks in edits. This module is the
// translation. Both the app and a browser send its output to the daemon's
// `apply_edit`, so neither Swift nor Python carries a copy of the rules.
//
// Pure functions with no DOM, so viewer/test/edits.test.js runs them under node.
(function (root) {
  const GESTURES = [
    "set_rollback",
    "extrude_face",
    "add_sketch",
    "extrude_sketch",
    "update_sketch",
    "update_feature",
    "new_document",
  ];

  function fail(message) {
    throw new Error(message);
  }

  function require_(message, ...keys) {
    const missing = keys.filter((key) => !(key in message));
    if (missing.length) fail(message.type + " needs " + missing.join(", "));
  }

  /// `f<n>`, counting on from the features that remain, skipping any id in use.
  function newId(kept) {
    const used = new Set(kept.map((feature) => feature.id));
    let number = kept.length + 1;
    while (used.has("f" + number)) number++;
    return "f" + number;
  }

  /// Building on top of a rolled-back part discards what was rolled back: replay
  /// only ever runs a prefix, so a hidden tail cannot sit in front of a live
  /// feature. Deleting from the end keeps every delete free of dependants.
  function dropRolledBackTail(state) {
    const kept = state.features.slice(0, state.rollbackIndex);
    const deletes = state.features
      .slice(kept.length)
      .reverse()
      .map((feature) => ({ op: "delete", id: feature.id }));
    return { deletes, kept };
  }

  /// The newest feature that is not a sketch: what a new extrude fuses into. A
  /// sketch makes no solid, and searching back from the sketch instead would
  /// name a solid that later features have already consumed.
  function lastSolidId(features) {
    for (let index = features.length - 1; index >= 0; index--) {
      if (features[index].type !== "sketch") return features[index].id;
    }
    return null;
  }

  const RULES = {
    set_rollback(state, message) {
      require_(message, "index");
      if (!Number.isInteger(message.index)) fail("'index' must be an integer");
      return [{ op: "set_rollback", index: Math.max(0, Math.min(message.index, state.features.length)) }];
    },

    extrude_face(state, message) {
      require_(message, "point", "normal", "distance");
      const { deletes, kept } = dropRolledBackTail(state);
      if (!kept.length) fail("there is no part to push or pull yet");
      const feature = {
        id: newId(kept),
        type: "extrude_face",
        base: kept[kept.length - 1].id,
        face: {
          from: "part",
          select: [{ op: "faces" }, { op: "nearest_to", point: message.point, normal: message.normal }],
        },
        distance: message.distance,
      };
      return [...deletes, { op: "insert", feature }];
    },

    add_sketch(state, message) {
      require_(message, "plane", "polygon");
      const { deletes, kept } = dropRolledBackTail(state);
      const feature = { id: newId(kept), type: "sketch", plane: message.plane, polygon: message.polygon };
      return [...deletes, { op: "insert", feature }];
    },

    extrude_sketch(state, message) {
      require_(message, "profile", "distance");
      const { deletes, kept } = dropRolledBackTail(state);
      if (!kept.some((feature) => feature.id === message.profile)) fail("unknown sketch id '" + message.profile + "'");
      const feature = { id: newId(kept), type: "extrude", profile: message.profile, distance: message.distance };
      const base = lastSolidId(kept);
      if (base !== null) feature.base = base;
      return [...deletes, { op: "insert", feature }];
    },

    update_sketch(state, message) {
      require_(message, "id", "polygon");
      const current = state.features.find((feature) => feature.id === message.id);
      if (!current || current.type !== "sketch") fail("no sketch with id '" + message.id + "'");
      // A sketch carries exactly one of rect, polygon and circle. Dragging a
      // corner turns any of them into a polygon.
      const { rect, circle, ...rest } = current;
      return [{ op: "update", id: message.id, feature: { ...rest, polygon: message.polygon } }];
    },

    update_feature(state, message) {
      require_(message, "id", "feature");
      return [{ op: "update", id: message.id, feature: message.feature }];
    },

    new_document(state) {
      const deletes = state.features
        .slice()
        .reverse()
        .map((feature) => ({ op: "delete", id: feature.id }));
      return [...deletes, { op: "set_rollback", index: 0 }];
    },
  };

  /// The edits for one gesture. `state` is {features, rollbackIndex}: the
  /// document as the viewer last loaded it. Throws an Error with the reason
  /// when the gesture makes no sense against that state.
  function editsFor(state, message) {
    if (!GESTURES.includes(message.type)) fail("unknown viewer command '" + message.type + "'");
    return RULES[message.type](state, message);
  }

  const api = { editsFor, GESTURES };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewerEdits = api;
})(typeof window !== "undefined" ? window : globalThis);
