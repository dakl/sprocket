A 30 x 10 x 1 mm board with a 5 x 2 grid of 1 x 1 x 2 mm pins, made from one
pin and a `pattern` with two linear directions (3 mm along X, 4 mm along Y).
The pin sits on an offset plane, `{"name": "XY", "offset": 1}`.

Proves: a feature pattern copies the tool's solid to every grid place, the
original included, and joins the copies to the base. It also covers an offset
origin plane.

Every `checks.json` value is computed by hand. Pin centres are x = 2, 5, 8, 11,
14 and y = 2, 6, z from 1 to 3:

    volume        = 30*10*1 + 10 * 1*1*2                              = 320
    surface area  = 680 (board) - 10 (footprints) + 10*4*2 (pin walls) + 10 (tops) = 760
    centre of mass = ((300*15 + 20*8) / 320, (300*5 + 20*4) / 320, (300*0.5 + 20*2) / 320)
                   = (14.5625, 4.9375, 0.59375)

Topology: 6 board faces plus 5 per pin is 56. Edges: 12 on the board and 12
per pin is 132. Vertices: 8 plus 8 per pin is 88. The board top carries 10
inner loops, so Euler-Poincare: 88 - 132 + 56 - 10 = 2.
