"""Where the BREP cache lives and how it prunes itself."""

import json
import os
import time
from pathlib import Path

from sprocket_engine import cache
from sprocket_engine.cache import BrepCache, default_cache_dir, prune
from sprocket_engine.cli import _build_document
from sprocket_engine.replay import replay

from golden_support import document

DAY = 24 * 3600


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_the_environment_variable_picks_the_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("SPROCKET_CACHE_DIR", str(tmp_path / "here"))
    assert default_cache_dir() == tmp_path / "here"


def test_without_the_variable_it_is_a_per_user_folder(monkeypatch):
    monkeypatch.delenv("SPROCKET_CACHE_DIR")
    folder = default_cache_dir()
    assert folder.parts[-2:] == ("sprocket", "brep")
    assert folder.is_relative_to(Path.home()) or "XDG_CACHE_HOME" in os.environ


def test_a_build_without_a_cache_dir_writes_nothing_beside_the_document(tmp_path):
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps(document("rect_extrude")))

    ok, output, _ = _build_document(path, None, None, None)

    assert ok, output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["part.sprocket"]
    assert list(default_cache_dir().glob("*.brep"))


def test_prune_drops_files_unused_for_a_month(tmp_path):
    old, fresh = tmp_path / "old.brep", tmp_path / "fresh.brep"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    _age(old, 31 * DAY)
    _age(fresh, 29 * DAY)

    assert prune(tmp_path) == 1
    assert not old.exists() and fresh.exists()


def test_prune_drops_the_oldest_until_it_fits(tmp_path):
    for age, name in enumerate(["newest", "middle", "oldest"]):
        path = tmp_path / f"{name}.brep"
        path.write_bytes(b"x" * 100)
        _age(path, age * 60)

    prune(tmp_path, max_bytes=250)

    assert sorted(p.stem for p in tmp_path.iterdir()) == ["middle", "newest"]


def test_prune_leaves_files_it_did_not_write(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("mine")
    _age(other, 365 * DAY)

    prune(tmp_path)

    assert other.exists()


def test_prune_sweeps_a_partial_write_left_by_a_crash(tmp_path):
    stale, live = tmp_path / "a.123.partial", tmp_path / "b.456.partial"
    stale.write_bytes(b"x")
    live.write_bytes(b"x")
    _age(stale, 2 * 3600)

    prune(tmp_path)

    assert not stale.exists() and live.exists()


def test_a_hit_counts_as_a_use(tmp_path):
    replay(document("rect_extrude"), cache_dir=tmp_path)
    for path in tmp_path.glob("*.brep"):
        _age(path, 31 * DAY)

    replay(document("rect_extrude"), cache_dir=tmp_path)

    assert prune(tmp_path) == 0


def test_a_file_pruned_by_another_process_is_a_miss(tmp_path):
    store = BrepCache(tmp_path)
    assert store.get("0" * 64) is None


def test_opening_a_cache_prunes_it_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cache, "prune", lambda folder: calls.append(folder))
    monkeypatch.setattr(cache, "_pruned", set())

    BrepCache(tmp_path)
    BrepCache(tmp_path)

    assert calls == [tmp_path]
