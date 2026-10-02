#!/bin/sh
# Copies the viewer and a self-contained Python with the engine into an app's
# Resources folder. Usage: bundle_engine.sh <path to Resources>
set -e

test -n "$1" || { echo "usage: bundle_engine.sh <Resources dir>"; exit 2; }
mkdir -p "$1"
RESOURCES="$(cd "$1" && pwd -P)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The viewer files the engine generates (part.mesh and friends) stay out.
# The app regenerates them in Caches.
rm -rf "$RESOURCES/viewer"
rsync -a \
    --exclude test --exclude cube-test.html --exclude build-planegcs.sh \
    --exclude part.mesh --exclude part.stl --exclude features.json --exclude schemas.json \
    "$ROOT/viewer/" "$RESOURCES/viewer/"

# uv's managed Pythons are standalone builds that run from any folder.
uv python install 3.12
# uv's folder for a Python is a symlink to the real install. Resolve it, or the
# cleanup below would delete files from uv's own copy.
PYTHON_HOME="$(cd "$(dirname "$(dirname "$(uv python find 3.12 --managed-python)")")" && pwd -P)"
rm -rf "$RESOURCES/python"
cp -R "$PYTHON_HOME" "$RESOURCES/python"
test ! -L "$RESOURCES/python" || { echo "python was copied as a symlink"; exit 1; }
rm -f "$RESOURCES"/python/lib/python3.12/EXTERNALLY-MANAGED

# Locked versions, without the dev group or the mcp extra.
REQUIREMENTS="$(mktemp)"
uv export --project "$ROOT/engine" --frozen --no-dev --no-emit-project --no-header > "$REQUIREMENTS"
uv pip install --quiet --python "$RESOURCES/python/bin/python3" --no-deps -r "$REQUIREMENTS"
uv pip install --quiet --python "$RESOURCES/python/bin/python3" --no-deps "$ROOT/engine"
rm -f "$REQUIREMENTS"

# Nothing the engine imports lives in these.
cd "$RESOURCES/python"
rm -rf include share lib/python3.12/test lib/python3.12/idlelib lib/python3.12/tkinter \
    lib/python3.12/turtledemo lib/python3.12/ensurepip
find . -name __pycache__ -type d -prune -exec rm -rf {} +
echo "bundled engine: $(du -sh "$RESOURCES/python" | cut -f1) python, $(du -sh "$RESOURCES/viewer" | cut -f1) viewer"
