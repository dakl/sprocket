A 20 x 20 x 10 mm box shelled to 2 mm walls with its top face removed: a tray.

Proves: `shell` hollows inward by `thickness` and opens the faces its selector names.

Every `checks.json` value is computed by hand. The cavity is 16 x 16 x 8 mm, from z = 2 to 10:

    volume           = 4000 - 16*16*8                       = 1952
    surface area     = 400 (bottom) + 800 (outer walls) + (400 - 256) (rim)
                       + 256 (floor) + 4*16*8 (inner walls)  = 2112
    centre of mass z = (4000*5 - 2048*6) / 1952              = 3.95082

Topology: outer bottom, 4 outer walls, the rim, the floor and 4 inner walls make
11 faces. 12 edges outside and 12 inside, 16 vertices. The rim has one inner
loop, so Euler-Poincare: 16 - 24 + 11 - 1 = 2.
