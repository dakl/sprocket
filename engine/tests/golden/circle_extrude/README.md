A circle of radius 10 mm, centred at (5, 0) on the XY plane, extruded 5 mm.

Proves: sketch (circle) -> extrude replays to a cylinder, and that the
sketch's `center` offsets the profile rather than being ignored. The
off-centre position is what makes the bounding box and centre of mass worth
asserting: a dropped `center` would still give the right volume.

Every `checks.json` value is computed by hand, independent of the engine's
own output. Volume = pi*10^2*5. Surface area = 2*pi*10^2 (two discs) +
2*pi*10*5 (the lateral face).

The topology counts follow from how OCCT represents a full revolution, not
from arithmetic: the lateral face is one periodic cylindrical surface closed
by a seam edge, so the solid has 3 faces (two discs plus the lateral), 3
edges (two circles plus the seam), and 2 vertices (the seam's ends, each
also closing one circle). This is the case that would break first if a
kernel upgrade changed that representation.
