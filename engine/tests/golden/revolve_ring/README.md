A 10 x 5 mm rectangle on the XZ plane, offset to span 10 to 20 mm from the Z
axis, revolved a full turn about Z. The result is a square-section ring: outer
radius 20, inner radius 10, height 5.

Proves: sketch -> revolve, and that the sketch `center` places the profile in
the plane's own uv axes rather than in world XY. On the XZ plane a centre of
[15, 2.5] has to mean x = 15, z = 2.5, and the bounding box below is what
catches it if that mapping is wrong.

Every `checks.json` value is computed by hand, independent of the engine's own
output:

    volume       = pi * (20^2 - 10^2) * 5            = 1500*pi
    surface area = 2 * pi * (20^2 - 10^2)   the two annular ends
                 + 2*pi*20*5                the outer wall
                 + 2*pi*10*5                the inner wall
                 = 900*pi

This is the first case whose solid is not topologically a ball, so the plain
V - E + F = 2 check the other cases use does not apply. It has 4 faces (two
annuli and two cylindrical walls), 6 edges (four circles plus a seam on each
wall) and 4 vertices (the two seams' ends). The Euler-Poincare form covers it,
counting the 6 boundary loops, the single shell and the genus of 1:

    V - E + F - (L - F) = 2 * (S - G)
    4 - 6 + 4 - (6 - 4) = 2 * (1 - 1) = 0

A revolve that swept the wrong way, or failed to close the seam, would break
that identity rather than just the counts.
