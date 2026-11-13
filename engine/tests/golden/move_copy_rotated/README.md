A 20 x 10 x 5 mm box standing on XY, centred on the origin, and a copy of it
made by `move` with `tool`: turned 90 degrees about Z, then shifted 30 mm
along X.

Proves: `move` with `tool` leaves the original and makes a new body; it
rotates before it translates (translating first would put the copy's centre
at (0, 30)); and both bodies go into the part.

Every `checks.json` value is computed by hand:

    volume         = 2 * 20*10*5                     = 2000
    surface area   = 2 * 2(20*10 + 20*5 + 10*5)      = 1400
    bounding box   = original x -10..10, y -5..5; the copy, turned, is
                     x -5..5, y -10..10, then +30 in x: x 25..35.
                     Together: [-10, -10, 0] to [35, 10, 5]
    centre of mass = two equal boxes centred at (0, 0, 2.5) and (30, 0, 2.5):
                     (15, 0, 2.5)

Topology: two separate boxes, so 2 solids, 12 faces (all planes), 24 edges,
16 vertices. Euler per box: 8 - 12 + 6 = 2.
