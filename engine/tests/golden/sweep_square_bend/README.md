A 4 x 4 mm square on the YZ plane, centred on the origin, swept along a path
sketched on XY: a 20 mm line along +X, then a tangent 90 degree arc of radius
10 about (20, 10) that ends at (30, 10) heading +Y.

Proves: `sweep` follows a path of more than one entity, keeps the square
upright on a flat path, and joins the straight and curved parts into one solid.

Every `checks.json` value is computed by hand. The square's centroid lies on
the path, so Pappus applies to both the volume and the side area:

    path length    L = 20 + 10 * pi/2                           = 20 + 5 pi
    volume           = 16 L                                     = 320 + 80 pi = 571.327...
    surface area     = perimeter * L + 2 caps = 16 L + 32       = 352 + 80 pi = 603.327...
    bounding box     x 0 to 32 (outer radius 12 at the arc's end), y -2 to 10, z -2 to 2
    centre of mass   straight part 320 at (10, 0); the bend is an annular sector,
                     radii 8 to 12, quarter turn, volume 80 pi, centroid on the
                     -45 degree bisector at d = (2/3)(12^3 - 8^3)/(12^2 - 8^2) * sin(pi/4)/(pi/4)
                     from (20, 10): (26.451, 3.549). Weighted: (17.2368, 1.5612, 0).

Topology: two end caps, two flat sides on the straight part, two cylinders
(radii 8 and 12) on the bend, and one flat top and one flat bottom. The top
edge of the square stays in the plane z = 2 the whole way, so it sweeps a
single planar face over both parts, with area 4 * 20 + (pi/4)(12^2 - 8^2) =
80 + 20 pi. That makes 8 faces: 6 planes and 2 cylinders. The first hand count
said 10, with the top and bottom split at the junction. The first run listed
the faces and showed that count was wrong, not the sweep; volume, area,
bounding box and centroid matched the hand numbers on that same run.

Edges: 4 per cap (8), 2 vertical edges where each flat side meets its cylinder,
and 4 outline edges on each of the top and bottom faces (8): 18. Vertices: 4
at each end and 4 at the junction: 12. V - E + F = 12 - 18 + 8 = 2.
