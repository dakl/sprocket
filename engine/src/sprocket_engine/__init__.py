import build123d
import OCP

# Pinned per the spec's Decisions table: OCCT output is stable within a
# version but reorders topology across upgrades, so this is part of every
# cache key and every golden case's expected result.
ENGINE_VERSIONS = {
    "build123d": build123d.__version__,
    "ocp": OCP.__version__,
}


def main() -> None:
    from .cli import main as cli_main

    cli_main()
