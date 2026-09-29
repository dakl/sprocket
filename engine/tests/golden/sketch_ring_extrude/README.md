A 40 x 20 mm rectangle with a circle of radius 4 mm at its centre. The
extrude picks only the region between them, the rectangle with a hole in it,
and pushes it 10 mm.

Proves: region detection finds the ring and the disc as two regions, and a
profile named by `bounded_by` picks the ring. Extruding the whole sketch, or
the disc, gives a different volume, so a profile pick that ignored
`bounded_by` fails here.

Every `checks.json` value is computed by hand, independent of the engine.
Ring area = 40*20 - pi*4^2 = 800 - 16*pi. Volume = area * 10. Surface area =
top and bottom (2 * area) + the four outer sides (perimeter 120 * 10) + the
hole's wall (2*pi*4 * 10 = 80*pi), so 1600 - 32*pi + 1200 + 80*pi.

The centre of mass is the middle of the rectangle, because the hole is
centred. Topology: top and bottom (planar, each with a hole), four planar
sides, and the hole's cylinder, so 7 faces (PLANE 6, CYLINDER 1). Edges: 12
for the box, one circle each at top and bottom, and the cylinder's seam, so
15. Vertices: 8 for the box plus one on each circle, so 10. The solid has a
through-hole, so it has genus 1 and plain Euler does not hold. The
Euler-Poincare form does: V - E + F - (L - F) = 2(S - G), and with L = 9
loops that is 10 - 15 + 7 - 2 = 0 = 2(1 - 1).
