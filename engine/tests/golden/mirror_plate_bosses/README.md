A 20 x 10 x 3 mm plate (x 0 to 20) with a 6 mm boss 5 mm tall at x = 14,
mirrored about the YZ plane with the body as both `tool` and `base`. The result
is a 40 x 10 x 3 mm plate with a boss at x = 14 and x = -14.

Proves: `mirror` reflects a whole body about an origin plane and joins it, and
the two plate halves fuse into one box (the seam at x = 0 leaves no face).

Every `checks.json` value is computed by hand:

    volume         = 40*10*3 + 2 * pi*3^2*5                  = 1200 + 90 pi = 1482.7433...
    surface area   = 2*(40*10 + 40*3 + 10*3) + 2 * 2 pi*3*5   = 1100 + 60 pi = 1288.4956...
                     (a boss's top disc replaces the plate area under it)
    centre of mass = (0, 0, (1200*1.5 + 90 pi * 5.5) / (1200 + 90 pi)) = (0, 0, 2.26276...)

Topology: the box has 6 faces, each boss a cylinder and a top disc, so 10
faces (8 planes, 2 cylinders). Edges: 12 on the box, and per boss two circles
and one cylinder seam, so 18. Vertices: 8 on the box and one per circle, so 12.
