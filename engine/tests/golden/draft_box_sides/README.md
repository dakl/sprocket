A 20 x 10 x 10 mm box (x -10 to 10, y -5 to 5, z 0 to 10) with its four sides
drafted 5 degrees about the XY plane. The pull direction is the plane's normal,
+Z, so the sides lean inward going up and the 20 x 10 base stays put.

Proves: `draft` tilts the selected faces about the neutral plane, in the
direction the schema promises for a positive angle.

Every `checks.json` value is computed by hand, with t = tan 5 degrees. At
height z each wall has moved in by z t, so the section is (20 - 2zt)(10 - 2zt):

    volume         = integral 0..10 of (200 - 60 z t + 4 z^2 t^2) dz
                   = 2000 - 3000 t + (4000/3) t^2                    = 1747.7397...
    surface area   = base 200 + top (20 - 20t)(10 - 20t) + four trapezoids of
                     slant height 10 / cos 5: (60 - 40t) * 10 / cos 5   = 917.7313...
    centre of mass = z = integral of z * section / volume
                   = (10000 - 20000 t + 10000 t^2) / volume           = 4.76431...
    bounding box   = the base, since nothing grows: (-10, -5, 0) to (10, 5, 10)

Topology: still a six-faced box with flat faces: 6 planes, 12 edges, 8 vertices.
