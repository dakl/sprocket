#!/bin/sh
# Starts `sprocket web` on a throwaway copy of the fixture document, so a test
# run never edits a checked-in file. The socket sits in the temp directory
# because a Unix socket path has to stay under about 100 bytes.
set -e
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
cp "$here/fixtures/box.json" "$work/box.sprocket"
# The solver extra is for the tests where an agent edits a sketch the viewer has open.
exec uv run --extra solver --directory "$here/../engine" sprocket web "$work/box.sprocket" \
  --port "${PORT:-8765}" --socket "$work/d.sock" --viewer-dir "$here/../viewer"
