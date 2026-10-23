"""The engine library versions a build depends on."""

from importlib import metadata

import OCP

# Pinned per the spec's Decisions table: OCCT output is stable within a
# version but reorders topology across upgrades, so this is part of every
# cache key and every golden case's expected result.
ENGINE_VERSIONS: dict[str, str] = {
    "build123d": metadata.version("build123d"),
    "ocp": OCP.__version__,
}
