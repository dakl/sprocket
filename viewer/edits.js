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
    "add_feature",
    "update_sketch",
    "update_sketch_entities",
    "update_feature",
    "set_body",
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
    /// Names, colours or finishes one body. Only the fields given change. Showing and
    /// hiding is view state, sent as the daemon's set_visibility instead.
    set_body(state, message) {
      require_(message, "id");
      const edit = { op: "set_body", id: message.id };
      for (const name of ["name", "color", "material"]) if (name in message) edit[name] = message[name];
      if (Object.keys(edit).length === 2) fail("set_body needs name, color or material");
      return [edit];
    },

    set_rollback(state, message) {
      require_(message, "index");
      if (!Number.isInteger(message.index)) fail("'index' must be an integer");
      return [{ op: "set_rollback", index: Math.max(0, Math.min(message.index, state.features.length)) }];
    },

    extrude_face(state, message) {
      // `face` is a selector the engine made from the pick (resolve_selection).
      // Without one, the face is named by where it was clicked.
      require_(message, "distance");
      if (!message.face) require_(message, "point", "normal");
      const { deletes, kept } = dropRolledBackTail(state);
      if (!kept.length) fail("there is no part to push or pull yet");
      const feature = {
        id: newId(kept),
        type: "extrude_face",
        base: kept[kept.length - 1].id,
        face: message.face || {
          from: "part",
          select: [{ op: "faces" }, { op: "nearest_to", point: message.point, normal: message.normal }],
        },
        distance: message.distance,
      };
      return [...deletes, { op: "insert", feature }];
    },

    add_sketch(state, message) {
      require_(message, "plane");
      // A drawing of entities, or one polygon.
      if (!message.entities && !message.polygon) fail("add_sketch needs entities or polygon");
      const { deletes, kept } = dropRolledBackTail(state);
      const shape = message.entities
        ? { entities: message.entities, constraints: message.constraints || [] }
        : { polygon: message.polygon };
      const feature = { id: newId(kept), type: "sketch", plane: message.plane, ...shape };
      return [...deletes, { op: "insert", feature }];
    },

    /// `profiles` names regions picked from sketches: [{sketch, bounded_by, inside?}].
    /// `profile` is the older form, one sketch extruded whole.
    extrude_sketch(state, message) {
      require_(message, "distance");
      if (!message.profile && !message.profiles) fail("extrude_sketch needs profile or profiles");
      const { deletes, kept } = dropRolledBackTail(state);
      const named = message.profiles ? message.profiles.map((profile) => profile.sketch) : [message.profile];
      for (const id of named) {
        if (!kept.some((feature) => feature.id === id && feature.type === "sketch")) fail("unknown sketch id '" + id + "'");
      }
      const feature = { id: newId(kept), type: "extrude", distance: message.distance };
      if (message.profiles) feature.profiles = message.profiles;
      else feature.profile = message.profile;
      const base = lastSolidId(kept);
      if (base !== null) feature.base = base;
      return [...deletes, { op: "insert", feature }];
    },

    /// A finished feature from a tool panel (fillet, chamfer, shell, hole and so
    /// on), without its id. The panel already named its `base`.
    add_feature(state, message) {
      require_(message, "feature");
      if (!message.feature.type) fail("add_feature needs a feature with a type");
      const { deletes, kept } = dropRolledBackTail(state);
      if (!kept.length) fail("there is no part yet");
      return [...deletes, { op: "insert", feature: { ...message.feature, id: newId(kept) } }];
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

    /// Redrawing an entity sketch: new entities and constraints, same plane and id.
    update_sketch_entities(state, message) {
      require_(message, "id", "entities");
      const current = state.features.find((feature) => feature.id === message.id);
      if (!current || current.type !== "sketch") fail("no sketch with id '" + message.id + "'");
      const { rect, circle, polygon, entities, constraints, ...rest } = current;
      return [
        {
          op: "update",
          id: message.id,
          feature: { ...rest, entities: message.entities, constraints: message.constraints || [] },
        },
      ];
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
