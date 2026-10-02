# Packaging plan

Status: proposal. Nothing here is built yet.
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
| 1 | Write the viewer's `part.mesh`, `features.json` and `schemas.json` to Application Support, not `viewer/` | `EngineDaemon.swift`, `cli.py` (`--viewer-dir`), `viewer/` fetch URLs | High: a signed bundle is read-only |
| 2 | Resolve paths from `Bundle.main.resourceURL`, not `#filePath` | `RepoPaths.swift`, `ContentView.swift`, `SprocketApp.swift` | Low |
| 3 | Launch the bundled Python, not `uv run` | `EngineDaemon.swift` line 54 | Low |
| 4 | Move `pytest`, `hypothesis` and IPython to a dev group | `engine/pyproject.toml` | Low |
| 5 | Build a release bundle, not a debug one | `app/package.sh` | Low |
| 6 | Strip tests, caches and unused OCP data from the bundle | new `app/bundle_engine.sh` | Medium: needs a size check |
| 7 | Sign, notarise and staple | new `app/release.sh` | Medium |
| 8 | Run the golden suite against the bundled interpreter | CI | Medium |

Item 1 is cheaper than it looks: `sprocket daemon` already takes `--viewer-dir`.

## Phases

1. **Relocatable (items 1 to 3).** The app runs from a copied folder with no `uv` and no repo.
   Exit check: copy the built `.app` to a clean user account, open `examples/box.sprocket`, edit a feature.
2. **Lean (items 4 to 6).** Measure the bundle. Record the installed size and the DMG size here.
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
- **Size.** The dev `.venv` is 650 MB and OCP is 224 MB of that. The target size is unmeasured.

## Out of scope

- Intel and universal builds.
- Mac App Store.
- Auto-update (Sparkle can follow).
- `.tcad` packages (M7) and Quick Look (M8). Both need this bundle first.

## Open questions

- Does the engine need `ocp-tessellate` at run time, or only for rendering and tests?
- Which OCP data files can be removed without breaking a fillet or a boolean?
- Is a paid Developer Program membership available for the release?
