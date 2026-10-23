"""The sweep op: a profile moved along a sketched path. The geometry itself is
the golden case sweep_square_bend; these cover combining and the errors.
"""

import math

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.regions import ProfileError
from sprocket_engine.replay import replay

BLOCK = [
    {
        "id": "bs",
        "type": "sketch",
        "plane": {"name": "XY", "offset": -5},
        "rect": {"width": 40, "height": 10, "center": [15, 0]},
    },
    {"id": "block", "type": "extrude", "profile": "bs", "distance": 10},
]
SECTION = {"id": "section", "type": "sketch", "plane": "YZ", "circle": {"radius": 1}}


def route(*entities):
    return {
        "id": "route",
        "type": "sketch",
        "plane": "XY",
        "entities": list(entities),
        "constraints": [],
    }


def line(entity_id, p0, p1, **extra):
    return {"id": entity_id, "kind": "line", "p0": p0, "p1": p1, **extra}


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def test_cut_mode_bores_the_swept_profile_out_of_the_base():
    features = BLOCK + [
        SECTION,
        route(line("e1", [0, 0], [20, 0])),
        {
            "id": "bore",
            "type": "sweep",
            "profile": "section",
            "path": {"sketch": "route"},
            "base": "block",
            "mode": "cut",
        },
    ]
    part, ctx = build(features)
    assert part.volume == pytest.approx(40 * 10 * 10 - math.pi * 20, rel=1e-6)
    assert [body.id for body in ctx.bodies] == ["block"]


def test_named_entities_pick_the_path_and_construction_is_left_out_by_default():
    """e2 is construction, so the default path is e1 alone. Naming both follows both."""
    sketch = route(
        line("e1", [0, 0], [10, 0]), line("e2", [10, 0], [10, 10], construction=True)
    )
    sweep = {
        "id": "pipe",
        "type": "sweep",
        "profile": "section",
        "path": {"sketch": "route"},
    }
    part, _ = build([SECTION, sketch, sweep])
    assert part.volume == pytest.approx(math.pi * 10, rel=1e-6)
    named = {**sweep, "path": {"sketch": "route", "entities": ["e1", "e2"]}}
    part, _ = build([SECTION, sketch, named])
    assert pytest.approx(10, abs=1e-6) == part.bounding_box().max.Y
    assert part.volume == pytest.approx(math.pi * 20, rel=1e-3), (
        "a mitred corner adds and removes the same wedge"
    )


def test_a_path_in_two_pieces_is_refused_by_name():
    sketch = route(line("e1", [0, 0], [10, 0]), line("e2", [20, 0], [30, 0]))
    with pytest.raises(ProfileError, match="2 separate pieces"):
        build(
            [
                SECTION,
                sketch,
                {
                    "id": "pipe",
                    "type": "sweep",
                    "profile": "section",
                    "path": {"sketch": "route"},
                },
            ]
        )


def test_the_path_sketch_cannot_be_deleted_while_a_sweep_uses_it():
    document = Document(
        [
            SECTION,
            route(line("e1", [0, 0], [10, 0])),
            {
                "id": "pipe",
                "type": "sweep",
                "profile": "section",
                "path": {"sketch": "route"},
            },
        ]
    )
    with pytest.raises(DocumentError, match="'pipe' still reference it"):
        document.apply_edits([{"op": "delete", "id": "route"}], None, "test")
