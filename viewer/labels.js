// Where the floating labels go (L, or Inspect > Labels).
//
// Each label starts a fixed leader length out from its anchor, pointing away
// from the middle of the view, so the labels fan out around the part instead
// of covering it. Rounds of pushing overlapping pairs apart then keep them
// readable. Each round also clamps every label into the view: a clamp done
// only at the end pushed labels crowded against an edge back on top of each
// other.
//
// Pure functions with no DOM, so viewer/test/labels.test.js runs them under node.
(function (root) {
  // How far out from its anchor a label starts, in screen pixels.
  const LEADER = 70;
  // The gap kept between two labels.
  const GAP = 4;
  // The gap kept from the edge of the view.
  const MARGIN = 6;
  const ROUNDS = 100;

  /// `anchors` is [{id, x, y, width, height}]: the anchor's screen point and
  /// the label's size. `view` is {left, top, width, height}. Returns
  /// [{id, x, y, width, height, anchorX, anchorY}] in the same order, with x
  /// and y the label's top-left corner.
  function layoutLabels(anchors, view) {
    const centreX = view.left + view.width / 2;
    const centreY = view.top + view.height / 2;
    const boxes = anchors.map((anchor, index) => {
      let dx = anchor.x - centreX;
      let dy = anchor.y - centreY;
      const length = Math.hypot(dx, dy);
      if (length < 1) {
        // An anchor on the centre has no direction of its own: spread such
        // anchors round the circle so they do not start on top of each other.
        const angle = -Math.PI / 4 + index * 2.4;
        dx = Math.cos(angle);
        dy = Math.sin(angle);
      } else {
        dx /= length;
        dy /= length;
      }
      return {
        id: anchor.id,
        width: anchor.width,
        height: anchor.height,
        anchorX: anchor.x,
        anchorY: anchor.y,
        cx: anchor.x + dx * LEADER,
        cy: anchor.y + dy * LEADER,
        // The range the label's centre may take and stay inside the view.
        minX: view.left + MARGIN + anchor.width / 2,
        maxX: view.left + view.width - MARGIN - anchor.width / 2,
        minY: view.top + MARGIN + anchor.height / 2,
        maxY: view.top + view.height - MARGIN - anchor.height / 2,
      };
    });
    const clampBox = (box) => {
      box.cx = clamp(box.cx, box.minX, box.maxX);
      box.cy = clamp(box.cy, box.minY, box.maxY);
    };
    boxes.forEach(clampBox);

    for (let round = 0; round < ROUNDS; round++) {
      let moved = false;
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i];
          const b = boxes[j];
          const overlapX = (a.width + b.width) / 2 + GAP - Math.abs(a.cx - b.cx);
          const overlapY = (a.height + b.height) / 2 + GAP - Math.abs(a.cy - b.cy);
          if (overlapX <= 0 || overlapY <= 0) continue;
          moved = true;
          // Push apart along the axis that needs the shorter move, unless a
          // sideways push would leave the view: then up and down. A tie (two
          // labels at one point) goes by index, so it is the same every frame.
          const signX = a.cx < b.cx ? -1 : 1;
          const fitsX = a.cx + (signX * overlapX) / 2 >= a.minX && a.cx + (signX * overlapX) / 2 <= a.maxX && b.cx - (signX * overlapX) / 2 >= b.minX && b.cx - (signX * overlapX) / 2 <= b.maxX;
          if (overlapY <= overlapX || !fitsX) {
            const sign = a.cy < b.cy || (a.cy === b.cy && i < j) ? -1 : 1;
            a.cy += (sign * overlapY) / 2;
            b.cy -= (sign * overlapY) / 2;
          } else {
            a.cx += (signX * overlapX) / 2;
            b.cx -= (signX * overlapX) / 2;
          }
        }
      }
      boxes.forEach(clampBox);
      if (!moved) break;
    }

    return boxes.map((box) => ({
      id: box.id,
      x: box.cx - box.width / 2,
      y: box.cy - box.height / 2,
      width: box.width,
      height: box.height,
      anchorX: box.anchorX,
      anchorY: box.anchorY,
    }));
  }

  /// The point on a label's edge nearest its anchor, where the leader line ends.
  function leaderEnd(label) {
    return {
      x: clamp(label.anchorX, label.x, label.x + label.width),
      y: clamp(label.anchorY, label.y, label.y + label.height),
    };
  }

  function clamp(value, low, high) {
    return high < low ? low : Math.min(high, Math.max(low, value));
  }

  const api = { LEADER, GAP, MARGIN, layoutLabels, leaderEnd };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FloatingLabels = api;
})(typeof window !== "undefined" ? window : globalThis);
