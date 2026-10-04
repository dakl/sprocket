A 20 x 20 x 10 mm box, then a 10 x 10 mm pad sketched on its bottom face
(z = 0) and extruded 4 mm downward with `mode: "join"`.

Proves: `mode` separates direction from combine. Without it, a base plus a
negative distance cuts, here into the air below the box, so the part could not
grow downward from a face.

Every `checks.json` value is computed by hand:

    volume           = 20*20*10 + 10*10*4                 = 4400
    surface area     = 1600 (box) - 100 (pad footprint) + 4*10*4 (pad sides) + 100 (pad bottom) = 1760
    centre of mass z = (4000*5 + 400*(-2)) / 4400          = 4.363636...

Topology: the box keeps 6 faces, its bottom now a ring with one inner loop. The
pad adds 4 sides and a bottom, so 11 faces. Edges: 12 on the box, 4 where the
pad meets the bottom, 4 vertical and 4 on the pad bottom, so 24. Vertices: 16.
Euler-Poincare with one inner loop (L - F = 1): 16 - 24 + 11 - 1 = 2.
