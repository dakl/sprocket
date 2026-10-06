A ruled loft from a 20 x 20 mm square on XY to a 10 x 10 mm square 10 mm
above it, both centred on the Z axis: a frustum of a square pyramid.

Proves: `loft` joins its sections in order, and `ruled` gives flat sides.

Every `checks.json` value is computed by hand, with A1 = 400, A2 = 100, h = 10:

    volume         = h/3 (A1 + A2 + sqrt(A1 A2))                 = 7000/3 = 2333.33...
    surface area   = 400 + 100 + 4 trapezoids. Each has parallel sides 20 and 10
                     and slant height sqrt(10^2 + 5^2) = sqrt(125), so
                     area = 500 + 4 * 15 * sqrt(125) = 500 + 60 sqrt(125) = 1170.82...
    centre of mass = (0, 0, h (A1 + 2 sqrt(A1 A2) + 3 A2) / (4 (A1 + sqrt(A1 A2) + A2)))
                   = (0, 0, 11000/2800) = (0, 0, 3.92857...)

Topology: a frustum is a box with a smaller top: 6 faces, 12 edges, 8
vertices. V - E + F = 8 - 12 + 6 = 2.

Face types are 2 PLANE and 4 BSPLINE, not 6 PLANE as first written. OCC's
ThruSections makes each ruled side a B-spline surface even when it is flat,
and the first run showed it with every other check passing. The sides are
still flat, so a sketch can go on one: `_face_plane` accepts a B-spline face
that `GeomLib_IsPlanarSurface` finds planar.
