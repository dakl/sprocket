# Packaging plan

Status: phase 1 is built (this PR). Phases 2 to 4 are not.
This plan replaces the "Packaging: deferred" row in `SPEC.md` once it is accepted.

## Goal

Ship Sprocket as one signed, notarised `.app` that runs on a Mac with no
`uv`, no Python and no source checkout.

## Decisions

| Part | Decision | Why |
| --- | --- | --- |
| Browser | Do not ship one | WKWebView is part of macOS. The viewer already uses it. |
| Viewer | Copy `viewer/` into the app's Resources | It is static files and already loads from `file://`. |
| Python | Ship a standalone CPython (python-build-standalone) | build123d needs OCP (the Python binding to the OpenCascade kernel). Nothing replaces it. |
| Dependencies | Install into the bundle with `uv pip install --target` | Plain files are easy to sign. |
| Freezing | Do not use PyInstaller or Nuitka at first | OCP loads many native modules, so a freeze is fragile. Revisit only if the bundle is too big. |
| Architecture | arm64 only | Intel wheels double the size and the test matrix. |
| Distribution | Developer ID signing, notarised DMG | The App Store sandbox restricts the subprocess and the Unix socket. |

## What has to change

| # | Change | Where | Risk |
| --- | --- | --- | --- |
| 1 | Write the viewer's `part.mesh`, `features.json` and `schemas.json` to Application Support, not `viewer/` | `AppPaths.prepareViewerDir()` copies the bundled viewer to `~/Library/Caches/dev.sprocket.app/viewer` on each launch | High: a signed bundle is read-only. Done, no viewer change needed |
| 2 | Resolve paths from `Bundle.main.resourceURL`, not `#filePath` | `AppPaths.swift` (was `RepoPaths.swift`) | Low. Done |
| 3 | Launch the bundled Python, not `uv run` | `AppPaths.engineLaunch` | Low. Done |
| 4 | Move `pytest`, `hypothesis` and IPython to a dev group | `engine/pyproject.toml` | Low. Done for `pytest` and `hypothesis`. IPython comes in through build123d, so it stays |
| 5 | Build a release bundle, not a debug one | `app/package.sh --bundle` | Low. Done |
| 6 | Strip tests, caches and unused OCP data from the bundle | `app/bundle_engine.sh` | Medium. Started: it removes Python's own test and GUI folders only |
| 7 | Sign, notarise and staple | new `app/release.sh` | Medium |
| 8 | Run the golden suite against the bundled interpreter | CI | Medium |

Item 1 needed no engine change: `sprocket daemon` already takes `--viewer-dir`.

## Phases

1. **Relocatable (items 1 to 3). Built.** The app runs from a copied folder with no `uv` and no repo.
   Checked so far: `app/package.sh --bundle` builds an app whose engine starts from the bundled Python with `env -i` (no `uv`, no `PATH` beyond `/usr/bin`), writes its viewer files to Caches and answers a JSON-RPC call. Not yet checked: a clean user account, and a full edit in the window.
   Exit check: copy the built `.app` to a clean user account, open `examples/box.sprocket`, edit a feature.
2. **Lean (items 4 to 6).** Measured 2026-10-02: the bundle is 618 MB installed. OCP is 223 MB, scipy 72 MB and numpy 22 MB. The DMG size is not measured.
3. **Signed (item 7).** Sign every Mach-O file (native binary), inner files first, the app last.
   Turn on the hardened runtime, then notarise with `notarytool`.
   Exit check: `spctl --assess` passes on a machine that never built the app.
4. **Verified (item 8).** See the golden-test risk below.

## Risks

- **Golden byte identity.** Layer 3 already depends on one compiled binary (CI finding, 2026-09-28).
  The bundled Python is another binary. Layers 1 and 2 must pass. Layer 3 may need its own `captured_with.json`.
- **Entitlements.** If every file is signed with one identity, library validation should not need an exception.
  CPython needs no JIT entitlement. This is untested.
- **First-launch time.** Importing build123d takes 2 to 3 seconds. Gatekeeper scanning may add to it on first run.
- **Licences.** OCCT is LGPL with an exception. Check the notice and relinking terms before a public release.
- **Size.** 618 MB installed is too large for a download. Phase 2 must cut it. Look at scipy first, and at unused OCP modules.
- **Orphan engine.** `kill` on the app (SIGTERM) leaves the engine running, because `shutdown()` only runs on a normal quit. This predates packaging.

## Out of scope

- Intel and universal builds.
- Mac App Store.
- Auto-update (Sparkle can follow).
- `.tcad` packages (M7) and Quick Look (M8). Both need this bundle first.

## Open questions

- Which package needs scipy, and can the engine do without it? (`ocp-tessellate` is needed: `mesh.py` imports it.)
- Which OCP data files can be removed without breaking a fillet or a boolean?
- Is a paid Developer Program membership available for the release?
