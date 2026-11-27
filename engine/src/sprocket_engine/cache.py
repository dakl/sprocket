"""Per-feature BREP (OCCT's native boundary-representation format) cache.

Cache key = hash(feature JSON + the keys of the features it names + pinned
engine versions). A cache hit skips re-running the op and restores the shape,
from memory when this process has seen it, else from disk.

The key is a content hash, so one directory serves every document. It lives in
the user's cache folder, not beside the document, and prunes itself.
"""

import hashlib
import json
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path

from build123d import Part, Shape, Sketch, export_brep, import_brep

from . import ENGINE_VERSIONS


def feature_cache_key(feature: dict, dependency_keys: list[str]) -> str:
    """The key of one feature's result.

    `dependency_keys` are the keys of the earlier features this one names (see
    replay.dependencies). Each of those covers what it names in turn, so the key
    covers everything the result can read. It does not cover the features in
    between: an edit to one body leaves the keys of every other body as they were.
    """
    payload = json.dumps(
        {"feature": feature, "depends_on": dependency_keys, "engine": ENGINE_VERSIONS},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# A file nobody has read or written for this long goes, and then the oldest
# go until the folder fits under the size cap.
MAX_AGE_SECONDS = 30 * 24 * 3600
MAX_BYTES = 1 << 30

# Pruning stats every file, so each process does it once per directory.
_pruned: set[Path] = set()

# Shapes this process has read or written, newest last, by (directory, key).
# Reading a BREP back costs about 1 ms a feature, which a 900-feature document
# pays on every rebuild. The directory is in the key so that a hit always means
# the file is on disk too: a new directory starts empty, as it does on disk.
MEMORY_ENTRIES = 4096
_memory: OrderedDict[tuple[Path, str], Shape] = OrderedDict()


def _remember(key: tuple[Path, str], shape: Shape) -> None:
    _memory[key] = shape
    _memory.move_to_end(key)
    while len(_memory) > MEMORY_ENTRIES:
        _memory.popitem(last=False)


def _fresh(shape: Shape) -> Shape:
    """A new wrapper on the same geometry, cast as `get` documents.

    The geometry (the TShape) is shared. The location handle and the Python
    object are new, so a caller that moves or labels what it got in place does
    not change what the next hit returns.
    """
    wrapped = shape.wrapped.Located(shape.wrapped.Location())
    return (Part if shape.solids() else Sketch)(wrapped)


def default_cache_dir() -> Path:
    """`$SPROCKET_CACHE_DIR`, else the platform's per-user cache folder."""
    if override := os.environ.get("SPROCKET_CACHE_DIR"):
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "sprocket" / "brep"


def prune(cache_dir: Path, max_age: float = MAX_AGE_SECONDS, max_bytes: int = MAX_BYTES) -> int:
    """Deletes stale `.brep` files and returns how many went.

    A hit touches its file, so mtime is the last use. Only `.brep` and `.partial`
    files are touched, so a `--cache-dir` that holds anything else keeps it.
    """
    # A write that crashed before its rename leaves a .partial behind.
    for path in cache_dir.glob("*.partial"):
        try:
            if path.stat().st_mtime < time.time() - 3600:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    entries = []
    for path in cache_dir.glob("*.brep"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    entries.sort(reverse=True)  # newest first

    cutoff = time.time() - max_age
    removed = 0
    total = 0
    for mtime, size, path in entries:
        total += size
        if mtime < cutoff or total > max_bytes:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


class BrepCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        resolved = cache_dir.resolve()
        self._resolved = resolved
        if resolved not in _pruned:
            _pruned.add(resolved)
            prune(cache_dir)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.brep"

    def get(self, key: str) -> Shape | None:
        """The cached shape cast back to the class its op returned.

        A cached shape has to come back as the build123d class its algebra operators expect,
        not the generic Compound that import_brep returns.

        The class is read off the shape rather than off the op that made it.
        This used to be a dict keyed by op type, and it named three of the
        seven registered ops, so a second replay of a fillet, chamfer, boolean
        or revolve raised KeyError on the op's own name. Nothing kept that dict
        in step with the op registry, and adding the four missing entries would
        have left the same trap for op number eight. The shape already knows:
        an op that produced a solid has solids, a sketch does not.
        """
        path = self._path(key)
        remembered = _memory.get((self._resolved, key))
        if remembered is not None and path.exists():
            _memory.move_to_end((self._resolved, key))
            os.utime(path)  # marks the hit for prune()
            return _fresh(remembered)
        try:
            os.utime(path)  # marks the hit for prune()
            shape = import_brep(path)
        except (FileNotFoundError, ValueError):
            # Missing, or pruned by another process between the two calls.
            return None
        _remember((self._resolved, key), shape)
        return _fresh(shape)

    def put(self, key: str, shape: Shape) -> None:
        """Writes to a temporary name and renames it into place.

        Every open document shares the folder, so a reader must never see half a
        file.
        """
        path = self._path(key)
        partial = path.with_suffix(f".{os.getpid()}.partial")
        export_brep(shape, partial)
        partial.replace(path)
        _remember((self._resolved, key), _fresh(shape))
