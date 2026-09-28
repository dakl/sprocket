The same box and the same selector chain as `fillet_box`, cut back 2 mm
instead of rounded.

The pair is the point. Fillet and chamfer take the same base, the same edges
and the same size, so they must agree on topology and differ only in the shape
of the four new faces. A chamfer wired to the fillet code, or the reverse,
passes neither case.

Every `checks.json` value is computed by hand. Each chamfered corner removes a
prism whose cross-section is a right isoceles triangle with 2 mm legs, so an
area of 2 mm^2:

    volume       = 8000 - 4 * 10 * 2                 = 7920
    surface area = 1584            top and bottom, each losing 4 * 2 mm^2
                 + 1040            the four flats, each 4 mm shorter
                 + 80*sqrt(2)      the four chamfer faces, 2*sqrt(2) wide
                 = 2624 + 80*sqrt(2)

Topology matches `fillet_box` exactly: 10 faces, 24 edges, 16 vertices, and
16 - 24 + 10 = 2. The chamfer faces are planar where the fillet's were
cylindrical, which changes no count.
