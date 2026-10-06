A 20 x 10 x 10 mm box (x -10 to 10, y -5 to 5, z 0 to 10) split by the plane
z = 4, keeping both sides: a 20 x 10 x 4 slab and a 20 x 10 x 6 block.

Proves: `split` cuts a body in two along a plane and keeps both pieces as
separate solids, with nothing lost or added.

Every `checks.json` value is computed by hand:

    volume         = 20 * 10 * 10, unchanged                   = 2000
    surface area   = the box's 2 * (200 + 200 + 100) = 1000, plus the two new
                     20 x 10 cut faces                         = 1400
    centre of mass = the box's own, (0, 0, 5): the pieces only part along z = 4.
                     Check: (800 * 2 + 1200 * 7) / 2000 = 5.

Topology: two boxes, each 6 planar faces, 12 edges and 8 vertices: 12, 24, 16.
For two solids V - E + F = 2 * 2: 16 - 24 + 12 = 4.
