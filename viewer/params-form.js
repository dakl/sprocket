// A parameter form generated from an op's JSON Schema, so a new op gets a form
// the moment it registers and nobody hand-writes one per op.
//
// The pure part (fieldsFor, parseField, applyField) has no DOM, so
// viewer/test/params-form.test.js runs it under node. render() is the thin DOM
// layer over it.
(function (root) {
  // The id names the feature and the type picks the schema, so neither is
  // something to edit in place.
  const HIDDEN = new Set(["id", "type"]);

  function kindOf(schema) {
    if (schema.const !== undefined) return null;
    if (schema.enum) return "select";
    if (schema.type === "number" || schema.type === "integer") return "number";
    if (schema.type === "string") return "text";
    if (schema.type === "boolean") return "checkbox";
    return "json";
  }

  /// The fields to show for one feature, in the schema's order.
  ///
  /// An optional object or array is shown only when the feature already has
  /// it. A sketch's rect, polygon and circle are alternatives, and offering all
  /// three would invite a document the schema rejects.
  function fieldsFor(schema, feature) {
    const required = new Set(schema.required || []);
    const fields = [];
    for (const [name, property] of Object.entries(schema.properties || {})) {
      if (HIDDEN.has(name)) continue;
      const kind = kindOf(property);
      if (kind === null) continue;
      if (kind === "json" && !required.has(name) && !(name in feature)) continue;
      fields.push({
        name,
        kind,
        required: required.has(name),
        description: property.description || "",
        options: property.enum || null,
        value: feature[name],
      });
    }
    return fields;
  }

  /// The text a field starts with.
  function textOf(field) {
    if (field.value === undefined) return "";
    if (field.kind === "json") return JSON.stringify(field.value);
    return String(field.value);
  }

  /// One field's text as a value. Returns {value}, {remove: true} for an empty
  /// optional field, or {error}.
  ///
  /// A number field also takes "=expression" over the document's parameters,
  /// which the engine resolves, so it stays a string here.
  function parseField(field, text) {
    const trimmed = text.trim();
    if (trimmed === "" && field.kind !== "checkbox") {
      return field.required ? { error: field.name + " is required" } : { remove: true };
    }
    if (field.kind === "number") {
      if (trimmed.startsWith("=")) return { value: trimmed };
      const number = Number(trimmed);
      return Number.isFinite(number)
        ? { value: number }
        : { error: field.name + " must be a number" };
    }
    if (field.kind === "checkbox") return { value: text === "true" };
    if (field.kind === "json") {
      try {
        return { value: JSON.parse(trimmed) };
      } catch (error) {
        // A bare word such as Z or XY is the common case for an axis or a
        // plane, and nobody should have to type the quotes.
        if (/^[A-Za-z_]+$/.test(trimmed)) return { value: trimmed };
        return { error: field.name + " is not valid JSON" };
      }
    }
    return { value: trimmed };
  }

  /// A copy of `feature` with one field changed. Returns {feature} or {error}.
  function applyField(feature, field, text) {
    const parsed = parseField(field, text);
    if (parsed.error) return { error: parsed.error };
    const next = { ...feature };
    if (parsed.remove) delete next[field.name];
    else next[field.name] = parsed.value;
    return { feature: next };
  }

  /// Draws the form into `container`. `onCommit(feature)` is called with the
  /// whole edited feature, and returns a promise that rejects with an Error
  /// whose message is shown under the form.
  function render(container, { schema, feature, onCommit, onClose }) {
    const doc = container.ownerDocument;
    container.innerHTML = "";

    const header = doc.createElement("div");
    header.className = "properties-header";
    const title = doc.createElement("span");
    title.textContent = feature.id + " · " + feature.type;
    const close = doc.createElement("button");
    close.textContent = "×";
    close.title = "Close";
    close.className = "properties-close";
    close.addEventListener("click", onClose);
    header.append(title, close);
    container.appendChild(header);

    if (schema.description) {
      const description = doc.createElement("div");
      description.className = "properties-description";
      description.textContent = schema.description;
      container.appendChild(description);
    }

    const errorEl = doc.createElement("div");
    errorEl.className = "properties-error";
    errorEl.dataset.testid = "properties-error";

    for (const field of fieldsFor(schema, feature)) {
      const row = doc.createElement("label");
      row.className = "properties-row";
      row.title = field.description;
      const name = doc.createElement("span");
      name.textContent = field.name;
      row.appendChild(name);

      let input;
      if (field.kind === "select") {
        input = doc.createElement("select");
        for (const option of field.options) {
          const item = doc.createElement("option");
          item.value = item.textContent = String(option);
          input.appendChild(item);
        }
        input.value = String(field.value);
      } else if (field.kind === "checkbox") {
        input = doc.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(field.value);
      } else if (field.kind === "json") {
        input = doc.createElement("textarea");
        input.rows = 3;
        input.spellcheck = false;
        input.value = textOf(field);
      } else {
        input = doc.createElement("input");
        input.type = "text";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.value = textOf(field);
      }
      input.name = field.name;
      input.dataset.testid = "field-" + field.name;

      input.addEventListener("change", () => {
        const text = field.kind === "checkbox" ? String(input.checked) : input.value;
        const result = applyField(feature, field, text);
        if (result.error) {
          errorEl.textContent = result.error;
          return;
        }
        errorEl.textContent = "";
        onCommit(result.feature).catch((error) => {
          errorEl.textContent = error.message;
        });
      });

      row.appendChild(input);
      container.appendChild(row);
    }
    container.appendChild(errorEl);
  }

  const api = { fieldsFor, parseField, applyField, textOf, render };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ParamsForm = api;
})(typeof window !== "undefined" ? window : globalThis);
