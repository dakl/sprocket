A 10 x 10 x 4 mm plate (x and y -5 to 5, z 0 to 4) with a 2.5 mm hole through
its centre, the tap drill for M3, and a cosmetic M3 thread on the hole.

Proves: a cosmetic `thread` accepts a tap-drill hole and leaves the solid
exactly as it was. Every number below is the plain drilled plate's.

Every `checks.json` value is computed by hand:

    volume         = 10 * 10 * 4 - pi 1.25^2 * 4                    = 400 - 6.25 pi = 380.365...
    surface area   = 2 (100 - pi 1.25^2) + 4 * 10 * 4 + 2 pi 1.25 * 4
                   = 200 - 3.125 pi + 160 + 10 pi                   = 360 + 6.875 pi = 381.598...
    centre of mass = (0, 0, 2), by symmetry

Topology: 6 planar faces and the hole's cylinder: 7 faces. Edges: 12 on the
box, the hole's two circles and its seam: 15. Vertices: 8 corners and one per
circle: 10. The hole makes the plate genus 1, and the top and bottom faces have
two loops each (L = 9), so V - E + F - (L - F) = 10 - 15 + 7 - 2 = 0 = 2(S - G).
