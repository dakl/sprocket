// The Modify tools' panels: which fields each tool asks for, and the feature
// its values make. Pure functions with no DOM, so viewer/test/modify.test.js
// runs them under node. index.html draws the fields and sends the feature.
((root) => {
  const POSITIVE = (value) => Number.isFinite(value) && value > 0;

  // `showWhen` names another field's value that must hold for this one to show.
  // A `dynamic` select has its choices made when the tool opens, from what the
  // document and the selection hold: {value, label} items in `target.options[name]`,
  // and what each value means in `target.choices[name][value]`.
  // `signed` takes any number but zero, `anyNumber` takes zero too, and
  // `integer` takes a whole number of at least `min`.
  const SAME = { value: "same", label: "This body" };
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
        {
          name: "closed",
          label: "No open face",
          type: "checkbox",
          value: false,
          onlyWithFace: true,
        },
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
        {
          name: "kind",
          label: "Kind",
          type: "select",
          options: ["simple", "counterbore", "countersink"],
          value: "simple",
        },
        { name: "diameter", label: "Diameter", type: "number", value: 4 },
        { name: "depth", label: "Depth", type: "number", value: 5, hideWhen: { through: true } },
        { name: "through", label: "Through", type: "checkbox", value: false },
        {
          name: "head_diameter",
          label: "Head dia",
          type: "number",
          value: 8,
          showWhen: { kind: ["counterbore", "countersink"] },
        },
        {
          name: "head_depth",
          label: "Head depth",
          type: "number",
          value: 3,
          showWhen: { kind: ["counterbore"] },
        },
        {
          name: "head_angle",
          label: "Angle",
          type: "number",
          value: 90,
          showWhen: { kind: ["countersink"] },
        },
      ],
    },
    plane: {
      title: "Offset plane",
      fields: [
        { name: "plane", label: "From", type: "select", dynamic: true, value: "" },
        { name: "offset", label: "Offset", type: "number", signed: true, value: 10 },
      ],
    },
    axis: { title: "Axis", fields: [] },
    point: {
      title: "Point",
      fields: [
        { name: "x", label: "X", type: "number", anyNumber: true, value: 0 },
        { name: "y", label: "Y", type: "number", anyNumber: true, value: 0 },
        { name: "z", label: "Z", type: "number", anyNumber: true, value: 0 },
      ],
    },
    mirror: {
      title: "Mirror",
      fields: [
        { name: "plane", label: "Plane", type: "select", dynamic: true, value: "" },
        { name: "into", label: "Into", type: "select", dynamic: true, value: "same" },
        {
          name: "mode",
          label: "Mode",
          type: "select",
          options: ["join", "cut"],
          value: "join",
          hideWhen: { into: "same" },
        },
      ],
    },
    linear: {
      title: "Linear pattern",
      fields: [
        { name: "direction", label: "Direction", type: "select", dynamic: true, value: "" },
        { name: "count", label: "Count", type: "number", integer: true, min: 2, value: 3 },
        { name: "spacing", label: "Spacing", type: "number", signed: true, value: 10 },
        { name: "into", label: "Into", type: "select", dynamic: true, value: "same" },
        {
          name: "mode",
          label: "Mode",
          type: "select",
          options: ["join", "cut"],
          value: "join",
          hideWhen: { into: "same" },
        },
      ],
    },
    circular: {
      title: "Circular pattern",
      fields: [
        { name: "axis", label: "Axis", type: "select", dynamic: true, value: "" },
        { name: "count", label: "Count", type: "number", integer: true, min: 2, value: 6 },
        { name: "angle", label: "Angle", type: "number", value: 360 },
        { name: "into", label: "Into", type: "select", dynamic: true, value: "same" },
        {
          name: "mode",
          label: "Mode",
          type: "select",
          options: ["join", "cut"],
          value: "join",
          hideWhen: { into: "same" },
        },
      ],
    },
    combine: {
      title: "Combine",
      fields: [
        { name: "target", label: "Target", type: "select", dynamic: true, value: "" },
        { name: "tool", label: "Tool", type: "select", dynamic: true, value: "" },
        {
          name: "mode",
          label: "Mode",
          type: "select",
          options: ["join", "cut", "intersect"],
          value: "join",
        },
      ],
    },
  };

  // The M6 ops. Each new body or combine choice is "Into": a new body, or a body the result joins, cuts or intersects.
  const NEW_BODY = { value: "new", label: "New body" };
  const NO_SECTION = { value: "none", label: "None" };
  const INTO_FIELDS = [
    { name: "into", label: "Into", type: "select", dynamic: true, value: "new" },
    {
      name: "mode",
      label: "Mode",
      type: "select",
      options: ["join", "cut", "intersect"],
      value: "join",
      hideWhen: { into: "new" },
    },
  ];
  /// ISO 261 coarse sizes the engine's thread op takes, with their nominal diameters in mm.
  const THREAD_SIZES = [
    ["M1.6", 1.6],
    ["M2", 2],
    ["M2.5", 2.5],
    ["M3", 3],
    ["M4", 4],
    ["M5", 5],
    ["M6", 6],
    ["M8", 8],
    ["M10", 10],
    ["M12", 12],
    ["M14", 14],
    ["M16", 16],
    ["M20", 20],
    ["M24", 24],
  ];
  Object.assign(TOOLS, {
    draft: {
      title: "Draft",
      fields: [
        { name: "neutral", label: "Neutral", type: "select", dynamic: true, value: "XY" },
        { name: "angle", label: "Angle", type: "number", signed: true, value: 3 },
      ],
    },
    split: {
      title: "Split",
      fields: [
        { name: "plane", label: "Plane", type: "select", dynamic: true, value: "" },
        {
          name: "keep",
          label: "Keep",
          type: "select",
          options: ["both", "above", "below"],
          value: "both",
        },
      ],
    },
    thread: {
      title: "Thread",
      fields: [
        {
          name: "designation",
          label: "Size",
          type: "select",
          options: THREAD_SIZES.map(([name]) => name),
          value: "M3",
        },
        { name: "modeled", label: "Modeled", type: "checkbox", value: false },
      ],
    },
    sweep: {
      title: "Sweep",
      fields: [
        { name: "path", label: "Path", type: "select", dynamic: true, value: "" },
        ...INTO_FIELDS,
      ],
    },
    loft: {
      title: "Loft",
      fields: [
        { name: "first", label: "From", type: "select", dynamic: true, value: "" },
        { name: "second", label: "To", type: "select", dynamic: true, value: "" },
        { name: "third", label: "Then", type: "select", dynamic: true, value: "none" },
        { name: "ruled", label: "Ruled", type: "checkbox", value: false },
        ...INTO_FIELDS,
      ],
    },
    primitive: {
      title: "Primitive",
      fields: [
        {
          name: "shape",
          label: "Shape",
          type: "select",
          options: ["box", "cylinder", "cone", "sphere", "torus"],
          value: "box",
        },
        { name: "plane", label: "Plane", type: "select", dynamic: true, value: "XY" },
        { name: "x", label: "Centre u", type: "number", anyNumber: true, value: 0 },
        { name: "y", label: "Centre v", type: "number", anyNumber: true, value: 0 },
        { name: "width", label: "Width", type: "number", value: 20, showWhen: { shape: ["box"] } },
        { name: "depth", label: "Depth", type: "number", value: 20, showWhen: { shape: ["box"] } },
        {
          name: "radius",
          label: "Radius",
          type: "number",
          value: 10,
          showWhen: { shape: ["cylinder", "cone", "sphere"] },
        },
        {
          name: "top_radius",
          label: "Top radius",
          type: "number",
          anyNumber: true,
          value: 0,
          showWhen: { shape: ["cone"] },
        },
        {
          name: "height",
          label: "Height",
          type: "number",
          value: 20,
          showWhen: { shape: ["box", "cylinder", "cone"] },
        },
        {
          name: "major_radius",
          label: "Ring radius",
          type: "number",
          value: 15,
          showWhen: { shape: ["torus"] },
        },
        {
          name: "minor_radius",
          label: "Tube radius",
          type: "number",
          value: 4,
          showWhen: { shape: ["torus"] },
        },
        ...INTO_FIELDS,
      ],
    },
    move: {
      title: "Move",
      fields: [
        { name: "copy", label: "Copy", type: "checkbox", value: false },
        { name: "dx", label: "X", type: "number", anyNumber: true, value: 0 },
        { name: "dy", label: "Y", type: "number", anyNumber: true, value: 0 },
        { name: "dz", label: "Z", type: "number", anyNumber: true, value: 0 },
        { name: "axis", label: "Turn about", type: "select", dynamic: true, value: "Z" },
        { name: "angle", label: "Angle", type: "number", anyNumber: true, value: 0 },
      ],
    },
  });

  // The sizes the engine reads for each primitive shape.
  const PRIMITIVE_SIZES = {
    box: ["width", "depth", "height"],
    cylinder: ["radius", "height"],
    cone: ["radius", "top_radius", "height"],
    sphere: ["radius"],
    torus: ["major_radius", "minor_radius"],
  };

  /// The smallest ISO size a cylinder of `diameter` mm can take: a hole fits
  /// sizes from its diameter up to 1.25 pitch over, a shaft up to half a pitch
  /// over, so the first nominal at or above the diameter suits both. M3 when unknown.
  function threadSize(diameter) {
    if (!POSITIVE(diameter)) {
      return "M3";
    }
    const found = THREAD_SIZES.find(([, nominal]) => nominal >= diameter - 1e-3);
    return found ? found[0] : THREAD_SIZES.at(-1)[0];
  }

  /// `base` and `mode` for a feature whose "Into" is a body, nothing for a new body.
  function intoFields(values) {
    if (values.into === "new") {
      return {};
    }
    return { base: values.into, ...(values.mode === "join" ? {} : { mode: values.mode }) };
  }

  /// The tools that make a construct feature, which builds no solid.
  const CONSTRUCT_KINDS = ["plane", "axis", "point"];

  /// Whether `field` is on the panel for `values`.
  function visible(field, values) {
    if (field.showWhen) {
      for (const [name, allowed] of Object.entries(field.showWhen)) {
        if (!allowed.includes(values[name])) {
          return false;
        }
      }
    }
    if (field.hideWhen) {
      for (const [name, hidden] of Object.entries(field.hideWhen)) {
        if (values[name] === hidden) {
          return false;
        }
      }
    }
    return true;
  }

  /// "2", "2.5" or "2.5 mm" as a number, NaN for anything else.
  function parseNumber(text) {
    const cleaned = String(text)
      .trim()
      .replace(/\s*mm$/i, "");
    return cleaned === "" ? Number.NaN : Number(cleaned);
  }

  /// A size rounded to a figure a person would type: a tenth below 2 mm, half
  /// a millimetre below 20, a millimetre above. `fallback` when it is not a size.
  function nice(value, fallback) {
    if (!POSITIVE(value)) {
      return fallback;
    }
    const step = value < 2 ? 0.1 : value < 20 ? 0.5 : 1;
    return Math.max(step, Math.round(Math.round(value / step) * step * 100) / 100);
  }

  /// Starting values that fit the part. `scale` is {box: [x, y, z]} for the
  /// body's bounding box and, for a hole, {face: [a, b]} for the picked face's
  /// two sides. Without it the values are the fixed ones below.
  /// Fillet and chamfer: 5% of the smallest box side. Shell: 8%. Hole: 15% of
  /// the face's smaller side, with its depth and head sized from the diameter
  /// in the proportions of the fixed 4 / 5 / 8 / 3.
  function defaults(kind, scale) {
    const values = Object.fromEntries(TOOLS[kind].fields.map((field) => [field.name, field.value]));
    // A dynamic choice starts on the first one offered, unless it names its own default.
    for (const field of TOOLS[kind].fields) {
      const offered = scale?.options?.[field.name];
      if (
        field.dynamic &&
        offered?.length &&
        !offered.some((item) => item.value === values[field.name])
      ) {
        values[field.name] = offered[0].value;
      }
    }
    if (kind === "thread" && scale && scale.diameter) {
      values.designation = threadSize(scale.diameter);
    }
    if (kind === "point" && scale && scale.at) {
      Object.assign(values, { x: scale.at[0], y: scale.at[1], z: scale.at[2] });
    }
    if (kind === "plane" && scale && scale.box) {
      values.offset = nice(Math.max(...scale.box) * 0.25, values.offset);
    }
    const smallest = scale?.box ? Math.min(...scale.box.filter(POSITIVE)) : Number.NaN;
    if (kind === "fillet") {
      values.radius = nice(smallest * 0.05, values.radius);
    }
    if (kind === "chamfer") {
      values.length = nice(smallest * 0.05, values.length);
    }
    if (kind === "shell") {
      values.thickness = nice(smallest * 0.08, values.thickness);
    }
    if (kind === "hole" && scale && scale.face) {
      const diameter = nice(Math.min(...scale.face) * 0.15, values.diameter);
      values.diameter = diameter;
      values.depth = nice(diameter * 1.25, values.depth);
      values.head_diameter = nice(diameter * 2, values.head_diameter);
      values.head_depth = nice(diameter * 0.75, values.head_depth);
    }
    return values;
  }

  /// The feature (without an id) for `kind` from the panel's `values`, or
  /// {error}. `target` is {base, edges?, open?, point?, normal?}: the feature
  /// it builds on and what was picked, already turned into selectors.
  function featureFor(kind, values, target) {
    for (const field of TOOLS[kind].fields) {
      if (field.type !== "number" || !visible(field, values)) {
        continue;
      }
      const value = parseNumber(values[field.name]);
      if (field.integer && !(Number.isInteger(value) && value >= field.min)) {
        return { error: `${field.label} must be a whole number, at least ${field.min}` };
      }
      if (field.integer) {
        continue;
      }
      if (field.anyNumber && !Number.isFinite(value)) {
        return { error: `${field.label} must be a number` };
      }
      if (field.anyNumber) {
        continue;
      }
      if (field.signed && !(Number.isFinite(value) && value !== 0)) {
        return { error: `${field.label} must be a number, not 0` };
      }
      if (field.signed) {
        continue;
      }
      if (!POSITIVE(value)) {
        return { error: `${field.label} must be a number above 0` };
      }
    }
    const number = (name) => parseNumber(values[name]);
    const choice = (name) => {
      const found = target.choices?.[name]?.[values[name]];
      return found === undefined ? null : found;
    };
    if (kind === "primitive") {
      const feature = { type: "primitive", shape: values.shape };
      const plane = choice("plane");
      if (plane !== null && plane !== "XY") {
        feature.plane = plane;
      }
      if (number("x") !== 0 || number("y") !== 0) {
        feature.at = [number("x"), number("y")];
      }
      for (const name of PRIMITIVE_SIZES[values.shape]) {
        feature[name] = number(name);
      }
      if (values.shape === "cone") {
        if (!(feature.top_radius >= 0)) {
          return { error: "Top radius must be 0 or more" };
        }
        if (feature.top_radius === 0) {
          delete feature.top_radius;
        }
      }
      if (values.shape === "torus" && feature.minor_radius >= feature.major_radius) {
        return { error: "Tube radius must be smaller than the ring radius" };
      }
      return { ...feature, ...intoFields(values) };
    }
    if (kind === "move") {
      const feature = { type: "move", [values.copy ? "tool" : "base"]: target.tool };
      const shift = ["dx", "dy", "dz"].map(number);
      if (shift.some((value) => value !== 0)) {
        feature.translate = shift;
      }
      if (number("angle") !== 0) {
        feature.rotate = { axis: choice("axis") ?? values.axis, angle: number("angle") };
      }
      if (!(feature.translate || feature.rotate)) {
        return { error: "Give a distance or an angle to move it by" };
      }
      return feature;
    }
    if (kind === "fillet") {
      return { type: "fillet", base: target.base, edges: target.edges, radius: number("radius") };
    }
    if (kind === "chamfer") {
      return { type: "chamfer", base: target.base, edges: target.edges, length: number("length") };
    }
    if (kind === "revolve") {
      const angle = number("angle");
      if (angle > 360) {
        return { error: "Angle must be 360 or less" };
      }
      return {
        type: "revolve",
        profile: target.profile,
        axis: values.axis,
        ...(angle === 360 ? {} : { angle }),
      };
    }
    if (kind === "shell") {
      const feature = { type: "shell", base: target.base, thickness: number("thickness") };
      if (target.open && !values.closed) {
        feature.open = target.open;
      }
      if (values.outward) {
        feature.outward = true;
      }
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
      if (values.through) {
        feature.through = true;
      } else {
        feature.depth = number("depth");
      }
      if (values.kind !== "simple") {
        feature.head_diameter = number("head_diameter");
        if (feature.head_diameter <= feature.diameter) {
          return { error: "Head dia must be larger than Diameter" };
        }
        if (values.kind === "counterbore") {
          feature.head_depth = number("head_depth");
        } else {
          feature.head_angle = number("head_angle");
        }
      }
      return feature;
    }
    const rounded = (value) => Math.round(value * 1e4) / 1e4;
    if (kind === "plane") {
      const plane = choice("plane");
      if (plane === null) {
        return { error: "Pick the plane to start from" };
      }
      return { type: "plane", plane, offset: number("offset") };
    }
    if (kind === "axis") {
      if (!target.axis) {
        return { error: "Pick a round face or an edge first" };
      }
      return { type: "axis", axis: target.axis };
    }
    if (kind === "point") {
      return {
        type: "point",
        at: [rounded(number("x")), rounded(number("y")), rounded(number("z"))],
      };
    }
    if (kind === "combine") {
      if (values.target === values.tool) {
        return { error: "Target and tool must be different bodies" };
      }
      const operation = { join: "union", cut: "subtract", intersect: "intersect" }[values.mode];
      return { type: "boolean", operation, base: values.target, tool: values.tool };
    }
    if (kind === "draft") {
      const angle = number("angle");
      if (!(Math.abs(angle) < 45)) {
        return { error: "Angle must be between -45 and 45" };
      }
      const neutral = choice("neutral");
      if (neutral === null) {
        return { error: "Pick the neutral plane" };
      }
      return { type: "draft", base: target.base, faces: target.faces, neutral, angle };
    }
    if (kind === "split") {
      const plane = choice("plane");
      if (plane === null) {
        return { error: "Pick the plane to split along" };
      }
      return {
        type: "split",
        base: target.tool,
        plane,
        ...(values.keep === "both" ? {} : { keep: values.keep }),
      };
    }
    if (kind === "thread") {
      return {
        type: "thread",
        base: target.base,
        face: target.face,
        designation: values.designation,
        ...(values.modeled ? { modeled: true } : {}),
      };
    }
    if (kind === "sweep") {
      if (!values.path) {
        return { error: "Pick the path sketch" };
      }
      if (values.path === target.profile) {
        return { error: "The path must be another sketch than the profile" };
      }
      return {
        type: "sweep",
        profile: target.profile,
        path: { sketch: values.path },
        ...intoFields(values),
      };
    }
    if (kind === "loft") {
      const sections = [values.first, values.second, values.third].filter(
        (id) => id && id !== "none",
      );
      if (sections.length < 2) {
        return { error: "Pick at least two sketches" };
      }
      if (new Set(sections).size !== sections.length) {
        return { error: "Each section must be a different sketch" };
      }
      return {
        type: "loft",
        sections,
        ...(values.ruled ? { ruled: true } : {}),
        ...intoFields(values),
      };
    }
    // Mirror and the patterns act on the picked body's solid. "Into" is the body the copies join or are cut from.
    const into = values.into === "same" ? target.tool : values.into;
    const mode = values.into !== "same" && values.mode === "cut" ? { mode: "cut" } : {};
    if (kind === "mirror") {
      const plane = choice("plane");
      if (plane === null) {
        return { error: "Pick the plane to mirror across" };
      }
      return { type: "mirror", tool: target.tool, base: into, plane, ...mode };
    }
    if (kind === "linear") {
      const direction = choice("direction");
      if (direction === null) {
        return { error: "Pick the direction" };
      }
      return {
        type: "pattern",
        tool: target.tool,
        base: into,
        linear: { direction, count: number("count"), spacing: number("spacing") },
        ...mode,
      };
    }
    if (kind === "circular") {
      const axis = choice("axis");
      if (axis === null) {
        return { error: "Pick the axis" };
      }
      const angle = number("angle");
      if (!(angle > 0 && angle <= 360)) {
        return { error: "Angle must be above 0 and at most 360" };
      }
      return {
        type: "pattern",
        tool: target.tool,
        base: into,
        circular: { axis, count: number("count"), ...(angle === 360 ? {} : { angle }) },
        ...mode,
      };
    }
    return { error: `unknown tool ${kind}` };
  }

  const api = {
    TOOLS,
    CONSTRUCT_KINDS,
    SAME,
    NEW_BODY,
    NO_SECTION,
    defaults,
    featureFor,
    nice,
    parseNumber,
    threadSize,
    visible,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.ViewerModify = api;
  }
})(typeof window === "undefined" ? globalThis : window);
