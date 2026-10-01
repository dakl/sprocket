#!/bin/sh
# Rebuilds planegcs.js, the constraint solver the sketcher runs in the viewer.
#
# planegcs is FreeCAD's 2D solver compiled to WebAssembly. Its npm package is
# ES modules plus a separate .wasm file. The Swift app loads the viewer from
# file://, where WebKit refuses module scripts and a fetched .wasm, so this
# bundles the glue and the wasm into one classic script that sets
# `Planegcs.createSolver`. Node can require it too, which is how
# viewer/test/sketch-solver.test.js runs the real solver.
#
# The build needs network once, for npm. The output is committed, like
# three.min.js, so nothing else does.
set -e
PLANEGCS_VERSION=1.2.0
ESBUILD_VERSION=0.25.10
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
echo '{"private":true,"type":"module"}' > package.json
npm install --silent "@salusoft89/planegcs@$PLANEGCS_VERSION" "esbuild@$ESBUILD_VERSION"
cat > entry.js <<'JS'
import init_planegcs_module from "@salusoft89/planegcs/dist/planegcs_dist/planegcs.js";
import { GcsWrapper } from "@salusoft89/planegcs/dist/sketch/gcs_wrapper.js";
import { SolveStatus, Algorithm } from "@salusoft89/planegcs/dist/planegcs_dist/enums.js";
import wasmBinary from "@salusoft89/planegcs/dist/planegcs_dist/planegcs.wasm";

export { SolveStatus, Algorithm };
export async function createSolver() {
  const module = await init_planegcs_module({ wasmBinary });
  return new GcsWrapper(new module.GcsSystem(), module);
}
JS
# import.meta.url is what the Emscripten glue reads to find its .wasm. The
# wasm is inlined, so any URL will do.
npx esbuild entry.js --bundle --format=iife --global-name=Planegcs --platform=browser \
  --loader:.wasm=binary --external:module '--define:import.meta.url="file:///vendor/"' \
  --minify \
  "--banner:js=/* @salusoft89/planegcs $PLANEGCS_VERSION (FreeCAD PlaneGCS compiled to WebAssembly), LGPL-2.0-or-later. Rebuild: viewer/vendor/build-planegcs.sh */" \
  '--footer:js=globalThis.Planegcs=Planegcs;' \
  --outfile="$here/planegcs.js"
ls -l "$here/planegcs.js"
