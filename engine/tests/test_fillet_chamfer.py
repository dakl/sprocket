"""The geometry of fillet and chamfer is pinned by the golden cases
(tests/golden/fillet_box, tests/golden/chamfer_box). What a golden case cannot
reach is a selector that fails, so that is what this covers.

Both ops share `resolve_edges`, so each test runs against both: a guard added
to one op and not the other is the mistake worth catching.
"""

import pytest

from sprocket_engine.model import JSONObject, ReplayDocument
from sprocket_engine.replay import replay
from sprocket_engine.selectors import SelectorError

BOX: list[JSONObject] = [
    {
        "id": "f1",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 40, "height": 20, "center": [0, 0]},
    },
    {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
]

SIZES: JSONObject = {"fillet": {"radius": 2}, "chamfer": {"length": 2}}


def document_with(op: str, selector: JSONObject) -> ReplayDocument:
    size = SIZES[op]
    assert isinstance(size, dict)
    return {
        "features": [
            *BOX,
            {"id": "f3", "type": op, "base": "f2", "edges": selector, **size},
        ],
    }


@pytest.mark.parametrize("op", sorted(SIZES))
def test_a_selector_matching_no_edges_fails_rather_than_doing_nothing(
    op: str,
) -> None:
    """An upstream edit can leave a stored selector matching nothing. Replaying
    it as a no-op gives back the part before the feature, which looks like the
    edit was never made rather than like it broke.
    """
    selector: JSONObject = {
        "from": "part",
        "select": [{"op": "edges"}, {"op": "slice", "start": 99}],
    }
    with pytest.raises(SelectorError, match="no edges"):
        _ = replay(document_with(op, selector))


@pytest.mark.parametrize("op", sorted(SIZES))
def test_a_selector_landing_on_faces_says_so(op: str) -> None:
    """`edges` is a field name, not something the schema can enforce: a chain
    starting with `faces` is a valid selector that this op cannot use.
    """
    selector: JSONObject = {
        "from": "part",
        "select": [{"op": "faces"}, {"op": "first"}],
    }
    with pytest.raises(SelectorError, match="expected edges"):
        _ = replay(document_with(op, selector))
