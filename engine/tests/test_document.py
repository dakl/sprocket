"""The editable document: revisions, all-or-nothing batches, and the rules
that stop an edit producing a timeline replay cannot walk.
"""

import json
from pathlib import Path

import pytest

from sprocket_engine.document import (
    REFERENCE_FIELDS,
    Document,
    DocumentError,
    StaleRevisionError,
    resolve_parameters,
)
from sprocket_engine.model import JSONObject
from sprocket_engine.ops import REGISTRY

SKETCH: JSONObject = {
    "id": "s1",
    "type": "sketch",
    "plane": "XY",
    "rect": {"width": 40, "height": 30},
}
EXTRUDE: JSONObject = {"id": "e1", "type": "extrude", "profile": "s1", "distance": 10}


def build() -> Document:
    document = Document()
    _ = document.apply_edits([{"op": "insert", "feature": SKETCH}], None, "test")
    _ = document.apply_edits([{"op": "insert", "feature": EXTRUDE}], None, "test")
    return document


def test_reference_fields_covers_every_schema_that_names_a_feature() -> None:
    """The one list has to keep step with seven separate op schemas.

    Read the schemas rather than trusting the tuple: a new op whose reference
    field is missing from REFERENCE_FIELDS passes every other test here and
    then lets a delete orphan the feature that depended on it.
    """
    naming = {
        field
        for op in REGISTRY.values()
        for field, spec in op.schema.get("properties", {}).items()
        if isinstance(spec, dict)
        and str(spec.get("description", "")).startswith("id of")
    }
    assert naming <= set(REFERENCE_FIELDS), (
        f"not in REFERENCE_FIELDS: {naming - set(REFERENCE_FIELDS)}"
    )


def test_a_sketch_whose_region_an_extrude_picked_cannot_be_deleted() -> None:
    document = Document()
    _ = document.apply_edits([{"op": "insert", "feature": SKETCH}], None, "test")
    picked: JSONObject = {
        "id": "e1",
        "type": "extrude",
        "profiles": [{"sketch": "s1", "bounded_by": []}],
        "distance": 10,
    }
    _ = document.apply_edits([{"op": "insert", "feature": picked}], None, "test")
    with pytest.raises(DocumentError, match="'e1' still reference it"):
        _ = document.apply_edits([{"op": "delete", "id": "s1"}], None, "test")


def test_a_stale_base_revision_is_refused_with_both_numbers() -> None:
    document = build()
    with pytest.raises(StaleRevisionError) as caught:
        _ = document.apply_edits([{"op": "set_rollback", "index": 1}], 0, "test")
    assert (caught.value.based_on, caught.value.current) == (0, 2)
    assert document.revision == 2


def test_a_batch_that_fails_partway_applies_none_of_it() -> None:
    document = build()
    circle: JSONObject = {
        "id": "s2",
        "type": "sketch",
        "plane": "XY",
        "circle": {"radius": 5},
    }
    with pytest.raises(DocumentError):
        _ = document.apply_edits(
            [
                {"op": "insert", "feature": circle},
                {"op": "insert", "feature": dict(EXTRUDE)},  # duplicate id
            ],
            None,
            "test",
        )
    assert [feature["id"] for feature in document.features] == ["s1", "e1"]
    assert document.revision == 2


def test_a_forward_reference_names_both_features() -> None:
    document = Document()
    with pytest.raises(DocumentError, match="'e1' references 's1'"):
        _ = document.apply_edits([{"op": "insert", "feature": EXTRUDE}], None, "test")


def test_moving_a_feature_in_front_of_what_it_references_is_refused() -> None:
    document = build()
    with pytest.raises(DocumentError, match="references 's1'"):
        _ = document.apply_edits([{"op": "move", "id": "e1", "index": 0}], None, "test")
    assert [feature["id"] for feature in document.features] == ["s1", "e1"]


def test_deleting_a_feature_something_still_uses_names_the_dependant() -> None:
    document = build()
    with pytest.raises(DocumentError, match="'e1' still reference"):
        _ = document.apply_edits([{"op": "delete", "id": "s1"}], None, "test")


def test_a_parameter_that_breaks_a_feature_is_rejected_before_it_lands() -> None:
    """A width of zero fails the sketch schema. Catching it at set_parameter
    beats a rebuild that fails with no obvious cause.
    """
    document = Document()
    _ = document.apply_edits(
        [{"op": "set_parameter", "name": "width", "value": 40}], None, "test"
    )
    _ = document.apply_edits(
        [
            {
                "op": "insert",
                "feature": {**SKETCH, "rect": {"width": "=width", "height": 30}},
            }
        ],
        None,
        "test",
    )
    with pytest.raises(DocumentError):
        _ = document.apply_edits(
            [{"op": "set_parameter", "name": "width", "value": 0}], None, "test"
        )
    assert document.parameters == {"width": 40}


def test_an_expression_reads_another_parameter() -> None:
    assert resolve_parameters({"width": 40, "height": "=width / 2"}) == {
        "width": 40,
        "height": 20.0,
    }


def test_for_replay_substitutes_parameters_into_the_features() -> None:
    document = Document(parameters={"width": 40})
    _ = document.apply_edits(
        [
            {
                "op": "insert",
                "feature": {**SKETCH, "rect": {"width": "=width", "height": 30}},
            }
        ],
        None,
        "test",
    )
    assert document.for_replay()["features"][0]["rect"]["width"] == 40.0


def test_timeline_reports_the_author_and_which_features_build() -> None:
    document = build()
    _ = document.apply_edits([{"op": "set_rollback", "index": 1}], None, "test")
    assert document.timeline() == [
        {"index": 0, "id": "s1", "type": "sketch", "built": True, "author": "test"},
        {"index": 1, "id": "e1", "type": "extrude", "built": False, "author": "test"},
    ]


def test_an_m0_document_loads_as_revision_zero(tmp_path: Path) -> None:
    path = tmp_path / "part.sprocket"
    _ = path.write_text(
        json.dumps({"features": [SKETCH, EXTRUDE], "rollback_index": 2})
    )
    document = Document.load(path)
    assert (document.revision, document.parameters, document.log) == (0, {}, [])


def test_a_truncated_document_reports_a_sentence_not_a_traceback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "part.sprocket"
    _ = path.write_text('{"features": [')
    with pytest.raises(DocumentError, match="not valid JSON"):
        _ = Document.load(path)


def test_save_round_trips_the_revision_and_log(tmp_path: Path) -> None:
    document = build()
    _ = document.save(tmp_path / "part.sprocket")
    reloaded = Document.load(tmp_path / "part.sprocket")
    assert reloaded.revision == 2
    assert [entry["action"] for entry in reloaded.log] == ["insert", "insert"]
