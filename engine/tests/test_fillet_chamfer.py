"""The geometry of fillet and chamfer is pinned by the golden cases
(tests/golden/fillet_box, tests/golden/chamfer_box). What a golden case cannot
reach is a selector that fails, so that is what this covers.

Both ops share `resolve_edges`, so each test runs against both: a guard added
to one op and not the other is the mistake worth catching.
"""

import pytest

from sprocket_engine.replay import replay
from sprocket_engine.selectors import SelectorError

BOX = [
    {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 20, "center": [0, 0]}},
    {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
]

SIZES = {"fillet": {"radius": 2}, "chamfer": {"length": 2}}


def document_with(op: str, selector: dict) -> dict:
    return {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [*BOX, {"id": "f3", "type": op, "base": "f2", "edges": selector, **SIZES[op]}],
    }


@pytest.mark.parametrize("op", sorted(SIZES))
def test_a_selector_matching_no_edges_fails_rather_than_doing_nothing(op):
    """An upstream edit can leave a stored selector matching nothing. Replaying
    it as a no-op gives back the part before the feature, which looks like the
    edit was never made rather than like it broke."""
    selector = {"from": "part", "select": [{"op": "edges"}, {"op": "slice", "start": 99}]}
    with pytest.raises(SelectorError, match="no edges"):
        replay(document_with(op, selector))


@pytest.mark.parametrize("op", sorted(SIZES))
def test_a_selector_landing_on_faces_says_so(op):
    """`edges` is a field name, not something the schema can enforce: a chain
    starting with `faces` is a valid selector that this op cannot use."""
    selector = {"from": "part", "select": [{"op": "faces"}, {"op": "first"}]}
    with pytest.raises(SelectorError, match="expected edges"):
        replay(document_with(op, selector))
