// How far one wheel event zooms the camera.
//
// The vendored OrbitControls zooms a fixed 5% per wheel event and ignores
// deltaY. A mouse notch is one event, but a trackpad swipe is dozens of small
// ones: 60 events zoomed by 1/0.95^60, about 22x, however short the swipe. Here the zoom follows the distance scrolled: one pixel of deltaY is
// the same zoom however many events carry it.
//
// Pure functions with no DOM, so viewer/test/wheel-zoom.test.js runs them under node.
((root) => {
  // At sensitivity 1, 100 px of scroll changes the camera distance by about 5%:
  // the step OrbitControls took for every event. Chrome reports a mouse notch
  // as 100 px.
  const ZOOM_PER_PIXEL = 0.0005;
  // The Settings slider's range. The value is a multiplier on ZOOM_PER_PIXEL.
  const MIN_SENSITIVITY = 0.25;
  const MAX_SENSITIVITY = 4;
  const DEFAULT_SENSITIVITY = 1;
  // One event never zooms more than this many pixels' worth. An accelerated
  // mouse wheel can report thousands of pixels in one event.
  const MAX_PIXELS_PER_EVENT = 400;
  // WheelEvent.deltaMode: DOM_DELTA_PIXEL, DOM_DELTA_LINE, DOM_DELTA_PAGE.
  const LINE_PIXELS = 16;

  function clampSensitivity(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return DEFAULT_SENSITIVITY;
    }
    return Math.min(MAX_SENSITIVITY, Math.max(MIN_SENSITIVITY, number));
  }

  /// deltaY in pixels, whatever unit the event used. `pageHeight` is the
  /// viewport height, for the rare page-mode event.
  function pixelDelta(deltaY, deltaMode, pageHeight) {
    if (deltaMode === 1) {
      return deltaY * LINE_PIXELS;
    }
    if (deltaMode === 2) {
      return deltaY * pageHeight;
    }
    return deltaY;
  }

  /// The factor to multiply the camera's distance to its target by. Above 1
  /// zooms out (positive deltaY, as OrbitControls had it), below 1 zooms in.
  function distanceScale(deltaY, deltaMode, pageHeight, sensitivity) {
    const pixels = pixelDelta(deltaY, deltaMode, pageHeight);
    const clamped = Math.max(-MAX_PIXELS_PER_EVENT, Math.min(MAX_PIXELS_PER_EVENT, pixels));
    return Math.exp(clamped * ZOOM_PER_PIXEL * clampSensitivity(sensitivity));
  }

  const api = {
    ZOOM_PER_PIXEL,
    MIN_SENSITIVITY,
    MAX_SENSITIVITY,
    DEFAULT_SENSITIVITY,
    MAX_PIXELS_PER_EVENT,
    clampSensitivity,
    pixelDelta,
    distanceScale,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.WheelZoom = api;
  }
})(globalThis);
