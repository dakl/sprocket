Two independent extrudes, the second subtracted from the first. A 40 x 20 x 10
mm box, less a 20 x 10 mm block centred at x = 20, which reaches 10 mm into one
end and runs the full height. The result is a box with a slot in one end face.

Proves: a boolean reaching two features that were never joined. The tool
extrude names no `base`, so nothing consumed it and it sits in the replay
results as a body in its own right. That is the whole reason this op takes
feature ids rather than a body selection.

Every `checks.json` value is computed by hand. The tool overlaps the box over
x 10..20, y -5..5, z 0..10, so it removes 10 * 10 * 10 mm3:

    volume        = 8000 - 1000                      = 7000
    centre of mass x = (8000*0 - 1000*15) / 7000     = -15/7

The surface area is 2800, which is also the plain box's surface area. A
full-height slot replaces wall area one for one: the end face and the two flat
faces give up 300 mm2 between them, and the slot's three new walls add 300 mm2
back. That coincidence is the case's most useful property. It shows surface
area alone is a weak fingerprint, and it is why the golden harness asserts
volume, bounding box, centroid and topology as well.

Topology: 10 faces, because the slot leaves the end face as two separate
strips rather than one face with a notch. Each flat face is now an eight-sided
loop, giving 8 vertices top and bottom and 8 vertical edges between them, so
16 vertices and 24 edges. Euler: 16 - 24 + 10 = 2.
