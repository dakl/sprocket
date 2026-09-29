A slot on the XY plane, extruded 6 mm: two 20 mm lines joined by two
semicircular arcs of radius 5 mm, drawn as the entities and constraints the
sketcher emits.

Proves: an entity sketch of lines and arcs replays to one region, and that
`extrude` with a `profile` extrudes it. The arcs are the point. A slot whose
arcs came out reversed or the wrong way round would still be closed, but its
volume and bounding box would not match.

Every `checks.json` value is computed by hand, independent of the engine.
Area = 20*10 (the rectangle between the arc centres) + pi*5^2 (the two half
discs). Volume = area * 6 = 1200 + 150*pi. Surface area = perimeter * 6 +
2 * area, where the perimeter is 2*20 + 2*pi*5, so 640 + 110*pi. The
bounding box runs from x = -5 to 25 because each arc bulges 5 mm past its
centre. The centre of mass is the middle of that symmetric shape, (10, 5, 3).

Topology: two planar faces top and bottom, two planar sides, two half
cylinders, so 6 faces (PLANE 4, CYLINDER 2). Each of the top and bottom has 4
edges and there are 4 vertical ones, so 12 edges, and 8 vertices. Euler:
8 - 12 + 6 = 2.
