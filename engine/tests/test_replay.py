import json
from pathlib import Path

import pytest

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
    document = {"features": [{"id": "f1", "type": "sketch", "plane": "XY"}]}  # missing "rect"
    with pytest.raises(ReplayError, match="failed validation"):
        replay(document)


def test_rollback_index_stops_before_the_extrude(tmp_path: Path):
    document = json.loads(json.dumps(RECT_EXTRUDE))
    document["rollback_index"] = 1  # replay only the sketch, not the extrude
    result = replay(document, cache_dir=tmp_path / "cache")
    assert result.area == pytest.approx(800, rel=1e-6)  # the sketch's own area, not a solid


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
