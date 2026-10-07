A 40 mm disc 3 mm thick with one 4 mm pin 5 mm tall at x = 12, copied into three
more places by a circular pattern about an axis *feature* (`spin`, the Z axis,
named as `{"construct": "spin"}`). The pin is a standalone extrude, so the
pattern consumes its body and joins four pins into the disc.

Proves: a construct axis is found by its feature id, the circular pattern turns
the tool about it (a 360 degree ring spaces copies by 360 / 4 = 90 degrees), and
the axis feature makes no body of its own.

Every `checks.json` value is computed by hand:

    volume         = pi*20^2*3 + 4 * pi*2^2*5      = 1200 pi + 80 pi = 1280 pi = 4021.2386
    surface area   = disc top (400 pi - 4 * 4 pi) + bottom 400 pi + side 2 pi*20*3 = 120 pi
                     + 4 * (pin side 2 pi*2*5 = 20 pi + pin top 4 pi)
                   = 384 pi + 400 pi + 120 pi + 96 pi = 1000 pi = 3141.5927
    centre of mass = (0, 0, (1200 pi*1.5 + 80 pi*5.5) / 1280 pi) = (0, 0, 1.75)
                     (the four pins sit at 90 degree steps, so x and y cancel)

Topology: the disc has 3 faces (top, bottom, side) and each pin a side and a
top, so 11 faces (6 planes, 5 cylinders). Edges: the disc has its two circles
and one seam, each pin its base circle, top circle and one seam, so 3 + 4 * 3 = 15.
Vertices: one per circle of the disc, whose seam ends are those two, and per
pin two seam ends, so 2 + 4 * 2 = 10.
