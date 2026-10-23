"""Per-feature BREP (OCCT's native boundary-representation format) cache.

Cache key = hash(feature JSON + upstream key + pinned engine versions).
A cache hit skips re-running the op and restores the shape from disk.
"""

import hashlib
import json
from pathlib import Path

from build123d import Compound, Part, Sketch
from OCP.TopoDS import TopoDS

from sprocket_engine.versions import ENGINE_VERSIONS
from sprocket_engine.b3d import export_brep, import_brep
from sprocket_engine.model import JSONObject


def feature_cache_key(feature: JSONObject, upstream_key: str) -> str:
    """The cache key of `feature` built on top of `upstream_key`."""
    payload = json.dumps(
        {"feature": feature, "upstream": upstream_key, "engine": ENGINE_VERSIONS},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class BrepCache:
    """One BREP file per feature result, named by its cache key."""

    def __init__(self, cache_dir: Path) -> None:
        """Use `cache_dir`, making it if it does not exist."""
        self.cache_dir: Path = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.brep"

    def get(self, key: str) -> Compound | None:
        """A cached shape has to come back as the build123d class its algebra
        operators expect, not the generic Compound that import_brep returns.

        The class is read off the shape rather than off the op that made it.
        This used to be a dict keyed by op type, and it named three of the
        seven registered ops, so a second replay of a fillet, chamfer, boolean
        or revolve raised KeyError on the op's own name. Nothing kept that dict
        in step with the op registry, and adding the four missing entries would
        have left the same trap for op number eight. The shape already knows:
        an op that produced a solid has solids, a sketch does not.
        """
        path = self._path(key)
        if not path.exists():
            return None
        shape = import_brep(path)
        # Every op's result is a Compound, so that is what the file holds.
        compound = TopoDS.Compound(shape.wrapped)
        return (Part if shape.solids() else Sketch)(compound)

    def put(self, key: str, shape: Compound) -> None:
        """Stores `shape` under `key`."""
        _ = export_brep(shape, self._path(key))
