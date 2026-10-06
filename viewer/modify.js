// The Modify tools' panels: which fields each tool asks for, and the feature
// its values make. Pure functions with no DOM, so viewer/test/modify.test.js
// runs them under node. index.html draws the fields and sends the feature.
(function (root) {
  const POSITIVE = (value) => Number.isFinite(value) && value > 0;

  // `showWhen` names another field's value that must hold for this one to show.
  const TOOLS = {
    fillet: {
      title: "Fillet",
      fields: [{ name: "radius", label: "Radius", type: "number", value: 1 }],
    },
    chamfer: {
      title: "Chamfer",
      fields: [{ name: "length", label: "Distance", type: "number", value: 1 }],
    },
    shell: {
      title: "Shell",
      fields: [
        { name: "thickness", label: "Thickness", type: "number", value: 2 },
        { name: "outward", label: "Outward", type: "checkbox", value: false },
        { name: "closed", label: "No open face", type: "checkbox", value: false, onlyWithFace: true },
      ],
    },
    revolve: {
      title: "Revolve",
      fields: [
        { name: "axis", label: "Axis", type: "select", options: ["X", "Y", "Z"], value: "Y" },
        { name: "angle", label: "Angle", type: "number", value: 360 },
      ],
    },
    hole: {
      title: "Hole",
      fields: [
        { name: "kind", label: "Kind", type: "select", options: ["simple", "counterbore", "countersink"], value: "simple" },
        { name: "diameter", label: "Diameter", type: "number", value: 4 },
        { name: "depth", label: "Depth", type: "number", value: 5, hideWhen: { through: true } },
        { name: "through", label: "Through", type: "checkbox", value: false },
        { name: "head_diameter", label: "Head dia", type: "number", value: 8, showWhen: { kind: ["counterbore", "countersink"] } },
        { name: "head_depth", label: "Head depth", type: "number", value: 3, showWhen: { kind: ["counterbore"] } },
        { name: "head_angle", label: "Angle", type: "number", value: 90, showWhen: { kind: ["countersink"] } },
      ],
    },
  };

  /// Whether `field` is on the panel for `values`.
  function visible(field, values) {
    if (field.showWhen) {
      for (const [name, allowed] of Object.entries(field.showWhen)) if (!allowed.includes(values[name])) return false;
    }
    if (field.hideWhen) {
      for (const [name, hidden] of Object.entries(field.hideWhen)) if (values[name] === hidden) return false;
    }
    return true;
  }

  /// "2", "2.5" or "2.5 mm" as a number, NaN for anything else.
  function parseNumber(text) {
    const cleaned = String(text).trim().replace(/\s*mm$/i, "");
    return cleaned === "" ? NaN : Number(cleaned);
  }

  function defaults(kind) {
    return Object.fromEntries(TOOLS[kind].fields.map((field) => [field.name, field.value]));
  }

  /// The feature (without an id) for `kind` from the panel's `values`, or
  /// {error}. `target` is {base, edges?, open?, point?, normal?}: the feature
  /// it builds on and what was picked, already turned into selectors.
  function featureFor(kind, values, target) {
    for (const field of TOOLS[kind].fields) {
      if (field.type === "number" && visible(field, values) && !POSITIVE(parseNumber(values[field.name]))) {
        return { error: field.label + " must be a number above 0" };
      }
    }
    const number = (name) => parseNumber(values[name]);
    if (kind === "fillet") return { type: "fillet", base: target.base, edges: target.edges, radius: number("radius") };
    if (kind === "chamfer") return { type: "chamfer", base: target.base, edges: target.edges, length: number("length") };
    if (kind === "revolve") {
      const angle = number("angle");
      if (angle > 360) return { error: "Angle must be 360 or less" };
      return { type: "revolve", profile: target.profile, axis: values.axis, ...(angle === 360 ? {} : { angle }) };
    }
    if (kind === "shell") {
      const feature = { type: "shell", base: target.base, thickness: number("thickness") };
      if (target.open && !values.closed) feature.open = target.open;
      if (values.outward) feature.outward = true;
      return feature;
    }
    if (kind === "hole") {
      const feature = {
        type: "hole",
        base: target.base,
        points: [target.point.map((value) => Math.round(value * 1e4) / 1e4)],
        direction: target.normal.map((value) => (value === 0 ? 0 : -Math.round(value * 1e6) / 1e6)),
        kind: values.kind,
        diameter: number("diameter"),
      };
      if (values.through) feature.through = true;
      else feature.depth = number("depth");
      if (values.kind !== "simple") {
        feature.head_diameter = number("head_diameter");
        if (feature.head_diameter <= feature.diameter) return { error: "Head dia must be larger than Diameter" };
        if (values.kind === "counterbore") feature.head_depth = number("head_depth");
        else feature.head_angle = number("head_angle");
      }
      return feature;
    }
    return { error: "unknown tool " + kind };
  }

  const api = { TOOLS, defaults, featureFor, parseNumber, visible };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewerModify = api;
})(typeof window !== "undefined" ? window : globalThis);
