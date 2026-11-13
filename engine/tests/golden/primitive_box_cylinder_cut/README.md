A 40 x 30 x 10 mm box standing on XY, centred on the origin, with a 5 mm
radius cylinder of the same height cut through it at (10, 0).

Proves: `primitive` builds a box and a cylinder standing on the plane, `at`
moves a primitive along the plane, and `base` with `mode: cut` combines it.

Every `checks.json` value is computed by hand:

    volume         = 40*30*10 - pi*5^2*10            = 12000 - 250 pi = 11214.60...
    surface area   = 2(40*30 + 40*10 + 30*10)         = 3800
                     - 2 * pi*5^2 (top and bottom)    = -50 pi
                     + 2*pi*5*10 (bore wall)          = +100 pi
                                                      = 3800 + 50 pi
    centre of mass = x: (12000*0 - 250 pi * 10) / (12000 - 250 pi) = -2500 pi / (12000 - 250 pi)
                     y: 0, z: 5 (both pieces are symmetric about z = 5)
    bounding box   = [-20, -15, 0] to [20, 15, 10]; the bore (x 5..15) is inside.

Topology: 6 box faces and the bore wall make 7 faces (6 planes, 1 cylinder).
Edges: 12 on the box, 2 circles and 1 cylinder seam, so 15. Vertices: 8
corners and one on each circle, so 10. Top and bottom each have an inner
loop, so L - F = 2. Euler-Poincare: 10 - 15 + 7 - 2 = 0 = 2(1 - 1).
