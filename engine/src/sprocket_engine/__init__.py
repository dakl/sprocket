from typing import NoReturn

from importlib import metadata

import OCP

# Pinned per the spec's Decisions table: OCCT output is stable within a
# version but reorders topology across upgrades, so this is part of every
# cache key and every golden case's expected result.
ENGINE_VERSIONS: dict[str, str] = {
    "build123d": metadata.version("build123d"),
    "ocp": OCP.__version__,
}


def main() -> NoReturn:
    from .cli import main as cli_main

    # cli.main returns the exit code (1 for a build that failed gracefully, so
    # the app and any shell caller can tell a failed build from a good one).
    # Dropping it here made every graceful failure exit 0.
    raise SystemExit(cli_main())
