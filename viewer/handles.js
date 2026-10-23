// Drag handles: the arrow that sets a distance and the ring that sets an
// angle, as Fusion shows them while a tool is open. The math is pure and
// takes plain [x, y, z] arrays, so viewer/test/handles.test.js runs it under
// node. The two builders take THREE as an argument and are only called in the
// browser. index.html owns the handle's state and the pointer events.
(function (root) {
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

  /// Where along the axis line origin + t * direction the pointer ray passes
  /// closest, as t. Both directions are unit vectors. Null when the ray runs
  /// along the axis: then every t is as close as every other.
  function axisParameter(rayOrigin, rayDirection, origin, direction) {
    const w = sub(origin, rayOrigin);
    const b = dot(direction, rayDirection);
    const denominator = 1 - b * b;
    if (denominator < 1e-6) return null;
    return (b * dot(rayDirection, w) - dot(direction, w)) / denominator;
  }

  /// The angle in degrees, 0 to 360, of `point` about `axis` through `centre`,
  /// counted from `reference` in the right-hand sense. `reference` is a unit
  /// vector square to `axis`.
  function angleAbout(point, centre, axis, reference) {
    const offset = sub(point, centre);
    const degrees = (Math.atan2(dot(offset, cross(axis, reference)), dot(offset, reference)) * 180) / Math.PI;
    return degrees < 0 ? degrees + 360 : degrees;
  }

  /// `raw` (0 to 360) moved by whole turns to sit nearest `previous`, so a
  /// drag past the reference keeps counting instead of jumping back to 0.
  function unwrapAngle(previous, raw) {
    return raw + 360 * Math.round((previous - raw) / 360);
  }

  /// The step a dragged value snaps to: the smallest of the usual steps that
  /// is at least four pixels of pointer travel, so zooming in gives finer
  /// values. `worldPerPixel` is millimetres (or degrees) per screen pixel.
  const STEPS = [0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100];
  function snapStep(worldPerPixel) {
    return STEPS.find((step) => step >= 4 * worldPerPixel) || STEPS[STEPS.length - 1];
  }

  /// `value` rounded to a whole number of `step`, without float residue.
  function snap(value, step) {
    return Math.round(Math.round(value / step) * step * 1e6) / 1e6;
  }

  /// A dragged value kept inside what the field accepts. `rule` is
  /// {min?, max?}: a positive-only field passes min, a signed one passes none.
  function clamp(value, rule) {
    let kept = value;
    if (rule.min !== undefined) kept = Math.max(rule.min, kept);
    if (rule.max !== undefined) kept = Math.min(rule.max, kept);
    return kept;
  }

  /// How the value box shows a number: at most two decimals, no trailing zeros.
  function format(value, unit) {
    const text = String(Math.round(value * 100) / 100);
    if (!unit) return text;
    return unit === "°" ? text + unit : text + " " + unit;
  }

  /// A unit vector square to `axis`, used when nothing better is known.
  function anyPerpendicular(axis) {
    const helper = Math.abs(axis[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
    const raw = cross(axis, helper);
    const length = Math.hypot(...raw);
    return raw.map((value) => value / length);
  }

  const HANDLE_COLOUR = 0x4aa3ff;
  const HOVER_COLOUR = 0x9fd0ff;

  function handleMaterial(THREE) {
    return new THREE.MeshBasicMaterial({ color: HANDLE_COLOUR, depthTest: false, transparent: true });
  }

  /// An arrow one unit long along +Y from the origin: a shaft, a cone, and a
  /// fatter invisible cylinder that is what the pointer hits. index.html
  /// scales it to a fixed size on screen every frame.
  function createArrow(THREE) {
    const group = new THREE.Group();
    const material = handleMaterial(THREE);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.6, 16), material);
    shaft.position.y = 0.3;
    const head = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.4, 24), material);
    head.position.y = 0.8;
    const grab = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.24, 1, 8), new THREE.MeshBasicMaterial({ visible: false }));
    grab.position.y = 0.5;
    for (const part of [shaft, head]) part.renderOrder = 1001;
    group.add(shaft, head, grab);
    group.userData = { material, grab: [grab, head, shaft] };
    return group;
  }

  /// The angle handle: an arc from the reference to the current angle, and a
  /// ball on its end that is what the pointer grabs. Built on the XY plane
  /// about +Z with radius 1; index.html orients it and redraws the arc.
  function createRing(THREE) {
    const group = new THREE.Group();
    const material = handleMaterial(THREE);
    const arc = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: HANDLE_COLOUR, depthTest: false, transparent: true }));
    const spoke = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: HANDLE_COLOUR, depthTest: false, transparent: true, opacity: 0.5 }));
    const knob = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), material);
    const grab = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    for (const part of [arc, spoke, knob]) part.renderOrder = 1001;
    group.add(arc, spoke, knob, grab);
    group.userData = { material, arc, spoke, knob, ball: grab, grab: [grab, knob] };
    return group;
  }

  /// The arc's points for `degrees`, on the unit circle in XY, one per 5°.
  function arcPoints(degrees) {
    const count = Math.max(2, Math.ceil(Math.abs(degrees) / 5) + 1);
    const points = [];
    for (let i = 0; i < count; i++) {
      const angle = ((degrees * i) / (count - 1) * Math.PI) / 180;
      points.push([Math.cos(angle), Math.sin(angle), 0]);
    }
    return points;
  }

  const api = { axisParameter, angleAbout, unwrapAngle, snapStep, snap, clamp, format, anyPerpendicular, arcPoints, createArrow, createRing, HANDLE_COLOUR, HOVER_COLOUR };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewerHandles = api;
})(typeof window !== "undefined" ? window : globalThis);
