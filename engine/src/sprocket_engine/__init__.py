"""Sprocket's geometry engine: a JSON op log replayed into a part with build123d."""

from typing import NoReturn

from sprocket_engine.versions import ENGINE_VERSIONS

__all__ = ["ENGINE_VERSIONS", "main"]


def main() -> NoReturn:
    """The `sprocket` command."""
    from sprocket_engine.cli import main as cli_main  # noqa: PLC0415 -- importing the package must not load the CLI and build123d with it

    # cli.main returns the exit code (1 for a build that failed gracefully, so
    # the app and any shell caller can tell a failed build from a good one).
    # Dropping it here made every graceful failure exit 0.
    raise SystemExit(cli_main())
