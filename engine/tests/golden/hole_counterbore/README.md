A 20 x 20 x 10 mm box with one counterbored through hole down the middle from
the top: a 4 mm bore, and an 8 mm head 3 mm deep.

Proves: `hole` places a counterbore at a point, drills along the default
direction (-Z) and `through` reaches the far side.

Every `checks.json` value is computed by hand:

    volume           = 4000 - pi*2^2*10 - pi*(4^2 - 2^2)*3        = 4000 - 76*pi
    surface area     = 1600 - pi*4^2 (top) - pi*2^2 (bottom)
                       + 2*pi*4*3 (head wall) + pi*(16 - 4) (head floor)
                       + 2*pi*2*7 (bore wall)                     = 1600 + 44*pi
    centre of mass z = (4000*5 - 40*pi*5 - 36*pi*8.5) / (4000 - 76*pi)

Topology: 6 box faces, the head wall, the head floor and the bore wall make 9
faces (7 planes, 2 cylinders). Edges: 12 on the box, 4 circles and 2 cylinder
seams, so 18. Vertices: 8 corners and one on each circle, so 12. The hole makes
genus 1, and the top, bottom and head floor each carry one extra loop, so
Euler-Poincare: 12 - 18 + 9 - 3 = 0 = 2(1 - 1).
