// The colour picker's rules: the palette, hex input, recent colours, and how a
// face colour becomes an `appearances` list for the daemon's set_appearances.
//
// Pure functions with no DOM, so viewer/test/colours.test.js runs them under node.
(function (root) {
  // Twelve tones a board or a printed part is usually made of.
  const PALETTE = [
    { name: "PCB green", hex: "#1f6b3a" },
    { name: "PCB blue", hex: "#2d68b0" },
    { name: "Black plastic", hex: "#1b1c1e" },
    { name: "White plastic", hex: "#f2f2ef" },
    { name: "Anodised aluminium", hex: "#a9b0b8" },
    { name: "Silver", hex: "#b8bcc2" },
    { name: "Gold", hex: "#d4af37" },
    { name: "Copper", hex: "#b87333" },
    { name: "Red", hex: "#d83a2e" },
    { name: "Orange", hex: "#e07b39" },
    { name: "Green", hex: "#2f9e44" },
    { name: "Blue", hex: "#2aa1d8" },
  ];

  // Finishes, as the engine names them (engine/src/sprocket_engine/materials.py).
  const MATERIALS = [
    { id: "matte_plastic", name: "Matte plastic" },
    { id: "glossy", name: "Glossy" },
    { id: "anodised_aluminium", name: "Anodised aluminium" },
    { id: "bare_metal", name: "Bare metal" },
  ];

  const RECENT_LIMIT = 6;

  /// "#rrggbb" in lower case from what a person typed ("A1B2C3", "#a1b2c3",
  /// "#abc"), or null when it is not a colour.
  function normaliseHex(text) {
    const value = String(text || "").trim().replace(/^#/, "").toLowerCase();
    if (/^[0-9a-f]{6}$/.test(value)) return "#" + value;
    if (/^[0-9a-f]{3}$/.test(value)) return "#" + value.split("").map((digit) => digit + digit).join("");
    return null;
  }

  /// The recent list with `colour` first, no repeats, at most RECENT_LIMIT.
  function pushRecent(recent, colour) {
    const hex = normaliseHex(colour);
    if (!hex) return recent.slice(0, RECENT_LIMIT);
    return [hex, ...recent.filter((item) => item !== hex)].slice(0, RECENT_LIMIT);
  }

  /// `info` is the daemon's get_face_appearance reply. An entry that colours
  /// this face alone was made for it, so a new colour replaces it rather than
  /// piling up behind it. Wider entries stay, and the new one wins by coming last.
  ///
  /// `change` is {color?, material?}. A field left out keeps what the face's
  /// own entry had; `material: null` removes the finish. `shown` is the
  /// colour on screen, used when there is no own entry and no new colour.
  function withFaceAppearance(appearances, info, change, shown) {
    const ownIndices = info.entries.filter((entry) => entry.faces === 1).map((entry) => entry.index);
    const own = ownIndices.length ? appearances[ownIndices[ownIndices.length - 1]] : null;
    const color = normaliseHex(change.color) || (own && own.color) || normaliseHex(shown);
    const material = "material" in change ? change.material : own && own.material;
    const entry = { color, select: info.selector };
    if (material) entry.material = material;
    const owned = new Set(ownIndices);
    return [...appearances.filter((_, index) => !owned.has(index)), entry];
  }

  function withFaceColour(appearances, info, colour) {
    return withFaceAppearance(appearances, info, { color: colour });
  }

  /// Drops every entry that colours this face. One that colours other faces
  /// too takes them with it, which `clearLabel` says up front.
  function withoutFaceColour(appearances, info) {
    const reaching = new Set(info.entries.map((entry) => entry.index));
    return appearances.filter((_, index) => !reaching.has(index));
  }

  /// The menu item for clearing, or null when the face has no colour of its own.
  function clearLabel(info) {
    if (!info || !info.entries.length) return null;
    const widest = Math.max(...info.entries.map((entry) => entry.faces));
    return widest > 1 ? "Clear colour (" + widest + " faces)" : "Clear face colour";
  }

  const api = { PALETTE, MATERIALS, RECENT_LIMIT, normaliseHex, pushRecent, withFaceColour, withFaceAppearance, withoutFaceColour, clearLabel };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewerColours = api;
})(typeof window !== "undefined" ? window : globalThis);
