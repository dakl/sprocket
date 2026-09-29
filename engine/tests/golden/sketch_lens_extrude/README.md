The same 40 x 20 mm rectangle, crossed by a circle of radius 6 mm centred on
its top side. The circle cuts three regions. The extrude picks the half disc
inside the rectangle and pushes it 4 mm.

Proves: two regions can share one `bounded_by`, and `inside` tells them
apart. The half disc outside the rectangle and the half disc inside are both
bounded by the top side (`e3`) and the circle (`e5`). The document names the
inner one with `inside: [20, 16]`. Picking the outer one instead moves the
whole part to y = 20..26, so the bounding box below fails if the tie-break
picks the wrong half.

Every `checks.json` value is computed by hand, independent of the engine.
Half disc area = pi * 6^2 / 2 = 18*pi. Volume = area * 4 = 72*pi. Perimeter =
the 12 mm chord plus half the circle (6*pi), so surface area = perimeter * 4 +
2 * area = 48 + 24*pi + 36*pi = 48 + 60*pi. The half disc's centroid sits
4r / (3*pi) = 8/pi below the chord, so the centre of mass is
(20, 20 - 8/pi, 2).

Topology: top and bottom, the flat side along the chord, and the half
cylinder, so 4 faces (PLANE 3, CYLINDER 1). Two edges each at top and bottom
and two vertical ones make 6 edges, with 4 vertices. Euler: 4 - 6 + 4 = 2.
