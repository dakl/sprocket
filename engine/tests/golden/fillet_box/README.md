The 40 x 20 x 10 mm box from `rect_extrude`, with its four vertical edges
rounded to a 2 mm radius.

Proves: a fillet driven by a selector chain, not by edge indices. The chain is
`edges` then `filter_by` Z, which is what makes the case worth having: it
matches four edges at once, so an op that quietly filleted only the first one
would still produce a plausible part.

Every `checks.json` value is computed by hand, independent of the engine's own
output. Each rounded corner removes a prism whose cross-section is a square
minus a quarter-disc, r^2 - pi*r^2/4:

    volume       = 8000 - 4 * 10 * (4 - pi)          = 7840 + 40*pi
    surface area = (1568 + 8*pi)   top and bottom
                 + 1040           the four flats, each 4 mm shorter
                 + 40*pi          the four quarter-cylinders
                 = 2608 + 48*pi

The bounding box does not move, because a fillet only cuts inward.

Topology: 6 original faces plus 4 fillet faces. Each fillet face is bounded by
two arcs and two straight edges, giving 8 arcs and 8 verticals, and the 8
horizontal edges of the box survive shortened, so 24 edges. Each of the top
and bottom faces is now an eight-sided loop, so 16 vertices. Euler checks
these against each other: 16 - 24 + 10 = 2.
