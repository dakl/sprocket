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

# Packages that nothing imports. The engine's suite imports everything else, so
# a package missing from this list is used. sympy and its mpmath come in through
# build123d's optional paths, and jedi and parso through IPython completion.
SITE=lib/python3.12/site-packages
rm -rf "$SITE/sympy" "$SITE/mpmath" "$SITE/jedi" "$SITE/parso" "$SITE/pip" "$SITE/cadquery_ocp_proxy"
rm -rf "$SITE"/sympy-* "$SITE"/mpmath-* "$SITE"/jedi-* "$SITE"/parso-* "$SITE"/pip-*

# threejs_materials ships 90 MB of PNG textures for glTF export. The engine
# exports STEP, STL and its own mesh, never glTF.
find "$SITE/threejs_materials" -name '*.png' -delete

# Test folders, build-time sources and type stubs. Nothing loads them at run time.
find "$SITE" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
find "$SITE" \( -name '*.pyx' -o -name '*.pxd' -o -name '*.pyi' -o -name '*.h' -o -name '*.c' \) -delete
find . -name __pycache__ -type d -prune -exec rm -rf {} +

# Drops local symbols from the native libraries. Exported symbols stay.
# strip breaks each file's signature, and an arm64 Mac kills a library with a
# bad one (exit 137 on `import OCP`). The ad-hoc signature here only keeps the
# files loadable. The release signing step replaces it.
find "$SITE" \( -name '*.so' -o -name '*.dylib' \) -print0 | xargs -0 strip -x 2>/dev/null || true
find "$SITE" \( -name '*.so' -o -name '*.dylib' \) -print0 | xargs -0 codesign --force -s - 2>/dev/null
echo "bundled engine: $(du -sh "$RESOURCES/python" | cut -f1) python, $(du -sh "$RESOURCES/viewer" | cut -f1) viewer"
