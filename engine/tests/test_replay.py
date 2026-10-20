import json
from pathlib import Path

import pytest
from golden_support import CASES
from golden_support import document as golden_document

from sprocket_engine import ops
from sprocket_engine.replay import ReplayError, replay

RECT_EXTRUDE = json.loads(
    (Path(__file__).parent / "golden" / "rect_extrude" / "document.json").read_text()
)


def test_unknown_op_type_raises():
    document = {"features": [{"id": "f1", "type": "not_a_real_op"}]}
    with pytest.raises(ReplayError, match="unknown op type"):
        replay(document)


def test_invalid_feature_raises():
    document = {
        "features": [{"id": "f1", "type": "sketch", "plane": "XY"}]
    }  # missing "rect"
    with pytest.raises(ReplayError, match="failed validation"):
        replay(document)


def test_rollback_index_stops_before_the_extrude(tmp_path: Path):
    document = json.loads(json.dumps(RECT_EXTRUDE))
    document["rollback_index"] = 1  # replay only the sketch, not the extrude
    result = replay(document, cache_dir=tmp_path / "cache")
    assert result.area == pytest.approx(
        800, rel=1e-6
    )  # the sketch's own area, not a solid


def test_rollback_index_zero_is_an_empty_document_not_an_error(tmp_path: Path):
    document = json.loads(json.dumps(RECT_EXTRUDE))
    document["rollback_index"] = 0  # before the first feature - a blank timeline
    result = replay(document, cache_dir=tmp_path / "cache")
    assert result is None


def test_cache_hit_skips_recompute(tmp_path: Path, monkeypatch):
    calls = []
    real_apply = ops.REGISTRY["extrude"].apply

    def counting_apply(feature, ctx):
        calls.append(feature)
        return real_apply(feature, ctx)

    monkeypatch.setattr(ops.REGISTRY["extrude"], "apply", counting_apply)

    cache_dir = tmp_path / "cache"
    replay(RECT_EXTRUDE, cache_dir=cache_dir)
    replay(RECT_EXTRUDE, cache_dir=cache_dir)

    assert len(calls) == 1  # second replay hit the cache, never called apply again


def test_changed_feature_invalidates_cache(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    first = replay(RECT_EXTRUDE, cache_dir=cache_dir)

    changed = json.loads(json.dumps(RECT_EXTRUDE))
    changed["features"][0]["rect"]["width"] = 80
    second = replay(changed, cache_dir=cache_dir)

    assert first.volume == pytest.approx(8000, rel=1e-6)
    assert second.volume == pytest.approx(16000, rel=1e-6)


@pytest.mark.parametrize("case", CASES)
def test_every_op_type_survives_its_own_cache(case, tmp_path: Path):
    """A cached shape has to come back as the same part the op produced, for
    every op, not just the ones somebody remembered to list.

    The other cache tests above all replay rect_extrude, so for a long time
    they only ever exercised sketch and extrude. The cache read used to pick a
    build123d class from a hand-written op-type map, which named three of the
    seven registered ops, so a second replay of a fillet, chamfer, boolean or
    revolve died with a bare KeyError on the op's own name. Nothing caught it:
    the golden suite passes no cache_dir, and examples/box.sprocket happens to use
    only the three types the map covered.

    Parametrising over the golden cases is the point rather than an accident.
    Between them they cover every op type a document can currently end on, and
    a new case is picked up here for free. A test naming the four broken types
    would have gone out of date the same way the map did.
    """
    document = golden_document(case)
    cache_dir = tmp_path / "cache"

    cold = replay(document, cache_dir=cache_dir)
    warm = replay(document, cache_dir=cache_dir)

    assert list(cache_dir.iterdir()), (
        "nothing was cached, so the second replay proves nothing"
    )
    assert type(warm) is type(cold)
    assert warm.volume == pytest.approx(cold.volume, rel=1e-9)
