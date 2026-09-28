# Golden cases

Each directory is one document plus everything needed to prove it still builds
the same part. `test_golden.py` runs three comparison layers over every case,
which is what SPEC.md asks for. The layers answer different questions, and only
the first one can tell you the engine is correct.

## The files

| File | Written by | What it is worth |
|------|-----------|------------------|
| `document.json` | hand | the input |
| `checks.json` | hand, **before the code existed** | proof of correctness |
| `expected.step` | captured from the engine | proof of sameness |
| `expected.stl` | captured from the engine | proof of sameness |
| `captured_with.json` | captured from the engine | which engine the two above belong to |
| `README.md` | hand | what the case proves, and the arithmetic |

`checks.json` is the only file that can fail for the right reason. Every number
in it was derived from the document by hand and cross-checked. `fillet_box`'s
volume is `7840 + 40*pi`, not whatever the engine printed. The two `expected.*`
files came out of the engine, so they can only ever say "same as the day it was
captured". A wrong part captured on day one passes layer 2 and layer 3 forever.

## The three layers

**Layer 1, fingerprint.** `checks.json` holds volume, surface area, bounding
box, centroid, and counts of solids, faces, edges and vertices, plus a
histogram of faces by surface type. The histogram is not decoration. A fillet
and a chamfer of the same size on the same box both give 10 faces, 24 edges and
16 vertices, so without `face_types` an op wired to the other one's code passes
both cases. `fillet_box` is `{CYLINDER: 4, PLANE: 6}` and `chamfer_box` is
`{PLANE: 10}`.

Topology counts were cross-checked with Euler's identity, `V - E + F = 2`.
`revolve_ring` is genus 1, so it needs the Euler-Poincare form instead:
`V - E + F - (L - F) = 2(S - G)`. A count that breaks the identity is wrong
before it is ever run.

**Layer 2, geometric equality.** The replayed part is cut against
`expected.step` in both directions and each symmetric difference must have
volume ~0. Both directions matter: a one-way cut also passes when the result is
a subset of the expectation, so a part missing a lump would slip through.

**Layer 3, byte identity.** The normalised STEP and the raw binary STL must
match byte for byte. It is the layer most likely to need an exemption after an
OCCT upgrade, and it runs only on the engine that captured the files.

That last part is measured, not cautious. CI failed five of these six cases on
Linux x86_64 against files captured on macOS arm64, with `build123d` 0.13.0 and
`ocp` 8.0.1.0.0 pinned identically by `uv.lock`, while layers 1 and 2 passed on
all six. `cadquery-ocp` ships one compiled OCCT per platform, five wheels for
that one version, so a version string cannot tell two of them apart. Volume and
topology are mathematical, so they survive a recompile. A byte hash is a
property of one binary's float formatting and meshing order.

So `captured_with.json` records `build123d`, `ocp`, the platform and the
machine, and the layer skips with the difference printed when they do not match.
Skipping is the honest outcome: the layer claims byte identity within one
compiled engine and nothing wider. On another machine it guards nothing, which
is why it is the third layer and not the only one.

`rect_extrude` did pass on Linux. One case agreeing across platforms is luck
about a simple box, not a reason to expect the others to.

STEP normalisation is one regex over the `FILE_NAME` timestamp. That is
measured, not assumed: two exports of one solid ten hours apart differed on
exactly one of 803 lines, and every float matched byte for byte. No STEP parser
is needed. `test_the_timestamp_scrub_is_narrow_enough_and_wide_enough` checks
the substitution against synthetic fragments, because capturing a case and
running the suite in the same second leaves the real files identical and the
scrub untested.

STL determinism needs the tessellation parameters pinned, so
`golden_support.py` sets them rather than taking build123d's defaults. A hash
that changes should mean the geometry moved, not that a library default did.

## The lock

**Never modify, regenerate or delete an existing case without Daniel's explicit
approval.** A golden file that an agent may rewrite when it disagrees is not a
golden file. `make golden-update CASE=<name>` refuses a case that already has
`expected.*` and exits 2:

```sh
make golden-update CASE=new_case              # fine, the case is new
make golden-update CASE=fillet_box            # refused, exit 2
make golden-update CASE=fillet_box APPROVED=1 # only after Daniel says so
```

`checks.json` is never touched by the updater at all, in any mode. It was
written by hand and it stays that way.

## Adding a case

1. Write `document.json`.
2. Compute every `checks.json` value by hand. Check the topology against Euler.
3. Write `README.md` with the arithmetic in it, so the next person can audit
   the numbers without redoing the derivation.
4. Run `make test`. Layer 1 must pass before anything is captured. If it fails,
   either the document or the arithmetic is wrong, and capturing now would lock
   in the error.
5. `make golden-update CASE=<name>` to capture the two `expected.*` files.

## Verifying the harness itself

A harness that cannot see a change reports clean and means nothing. Both checks
below were run on 2026-09-28. Revert with an explicit edit afterwards and check
the file's hash, rather than `git checkout`.

- Changing `fillet_box`'s radius from 2 to 2.1 fails all three layers.
- Changing one `captured_with.json` platform to `Linux` skips exactly that
  case's byte test, with the difference in the skip reason, and leaves the other
  25 tests running.

There is no `expected.png`. SPEC.md lists one in the case-directory layout, but
it is not one of the three comparison layers and the spec itself says to keep
human-review cases rare. All six cases here have analytical checks, so none of
them needs an eyeball.
