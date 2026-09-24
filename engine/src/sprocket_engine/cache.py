"""Per-feature BREP (OCCT's native boundary-representation format) cache.

Cache key = hash(feature JSON + upstream key + pinned engine versions).
A cache hit skips re-running the op and restores the shape from disk.
"""

import hashlib
import json
from pathlib import Path

from build123d import Part, Shape, Sketch, export_brep, import_brep

from . import ENGINE_VERSIONS

# Each op type rewraps its cached shape as the build123d class its algebra
# operators expect, not the generic base Shape that import_brep returns.
RESULT_TYPE = {"sketch": Sketch, "extrude": Part, "extrude_face": Part}


def feature_cache_key(feature: dict, upstream_key: str) -> str:
    payload = json.dumps(
        {"feature": feature, "upstream": upstream_key, "engine": ENGINE_VERSIONS},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class BrepCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.brep"

    def get(self, key: str, op_type: str) -> Shape | None:
        path = self._path(key)
        if not path.exists():
            return None
        return RESULT_TYPE[op_type](import_brep(path).wrapped)

    def put(self, key: str, shape: Shape) -> None:
        export_brep(shape, self._path(key))
