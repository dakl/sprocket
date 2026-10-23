# Packaging plan

Status: phases 1 and 2 are built (this PR). Phases 3 and 4 are not.
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
| 5 | Build a release bundle, not a debug one | `app/package.sh --bundle`. `app/make_dmg.sh` packs `Sprocket.app` and an Applications shortcut into `.build/Sprocket.dmg` | Low. Done |
| 6 | Strip tests, caches and unused OCP data from the bundle | `app/bundle_engine.sh` | Medium. Done once: see phase 2 for what it removes and what remains |
| 7 | Sign, notarise and staple | new `app/release.sh` | Medium |
| 8 | Run the golden suite against the bundled interpreter | CI | Medium |

Item 1 needed no engine change: `sprocket daemon` already takes `--viewer-dir`.

## Phases

1. **Relocatable (items 1 to 3). Built.** The app runs from a copied folder with no `uv` and no repo.
   Checked so far: `app/package.sh --bundle` builds an app whose engine starts from the bundled Python with `env -i` (no `uv`, no `PATH` beyond `/usr/bin`), writes its viewer files to Caches and answers a JSON-RPC call. Not yet checked: a clean user account, and a full edit in the window.
   Exit check: copy the built `.app` to a clean user account, open `examples/box.sprocket`, edit a feature.
2. **Lean (items 4 to 6). Built, off by default.** The prune runs only with `app/package.sh --bundle --prune`. The default bundle stays unpruned for now. Measured 2026-10-02:

   | Step | Installed | DMG (UDZO) |
   | --- | --- | --- |
   | Unpruned (default) | 607 MB | 356 MB |
   | Pruned (`--prune`) | 435 MB | 211 MB |

   The prune removes sympy, mpmath, jedi, parso and pip (unused), the 90 MB of `threejs_materials` PNG textures (glTF export only), test folders, build-time sources and local symbols (`strip -x`).
   `strip` breaks each library's signature and an arm64 Mac then kills it, so the script re-signs ad hoc.
   The engine suite passes on the pruned bundled Python: 228 passed.
   What is left: OCP 221 MB, scipy 58 MB, sklearn 28 MB, numpy 15 MB, PIL 12 MB, ezdxf 10 MB, Python itself 40 MB.
   The engine uses none of scipy and sklearn directly. build123d imports them at load time (`brep_from_stl.py`, `objects_part.py` and others), so removing them means patching build123d. OCP's libraries are linked into one extension, so they cannot be dropped one by one.
3. **Signed (item 7).** Sign every Mach-O file (native binary), inner files first, the app last.
   Turn on the hardened runtime, then notarise with `notarytool`.
   Exit check: `spctl --assess` passes on a machine that never built the app.
   `release.yml` builds the DMG on a `macos-14` runner for each release and adds it to the release. A run with `release` set builds an existing release's DMG again from its tag. It is unsigned. A pull-request run on 2026-10-02 passed in 51 s and produced a 275 MB artifact. Nobody has opened that DMG on a Mac yet.
4. **Verified (item 8).** See the golden-test risk below.

## Risks

- **Golden byte identity.** Layer 3 already depends on one compiled binary (CI finding, 2026-09-28).
  The bundled Python is another binary. Layers 1 and 2 must pass. Layer 3 may need its own `captured_with.json`.
- **Entitlements.** If every file is signed with one identity, library validation should not need an exception.
  CPython needs no JIT entitlement. This is untested.
- **First-launch time.** Importing build123d takes 2 to 3 seconds. Gatekeeper scanning may add to it on first run.
- **Licences.** OCCT is LGPL with an exception. Check the notice and relinking terms before a public release.
- **Size.** Unpruned: 607 MB installed and a 356 MB DMG. Pruned: 435 MB and 211 MB. Large for a CAD tool download but not blocking. The next cut is a patched build123d without scipy and sklearn (about 85 MB). It is not planned.
- **Orphan engine.** `kill` on the app (SIGTERM) leaves the engine running, because `shutdown()` only runs on a normal quit. This predates packaging.

## Auto-update

Sparkle 2 (SwiftPM, `app/Sources/SprocketApp/Updater.swift`), tracked in #52.

- Only a release bundle (`package.sh --bundle`) has `SUFeedURL` and `SUPublicEDKey` in its `Info.plist`. A source build (`make app`, `package.sh` without `--bundle`) has neither, so it never checks and never replaces itself.
- The feed is `appcast.xml` on the latest release of the public repo `dakl/sprocket-releases`, which holds only DMGs and the feed. The source stays private.
- Each update is a DMG signed with an EdDSA key. The private key is in the login keychain of the Mac that made it (account `sprocket`). `generate_keys --account sprocket -x <file>` exports it.
- `CFBundleVersion` is the release version too. Sparkle compares that field.
- `package.sh` copies `Sparkle.framework` into `Contents/Frameworks` and signs it ad hoc with `--deep`, which reaches its XPC services and `Autoupdate`.

Checked on 2026-10-20 with a localhost feed: an ad-hoc signed release bundle found a newer one, downloaded it, installed it on quit and left no engine running. The new app started. The installed copy had no quarantine attribute. The real feed and `release.yml` are not tested yet.

## Out of scope

- Intel and universal builds.
- Mac App Store.
- `.tcad` packages (M7) and Quick Look (M8). Both need this bundle first.

## Open questions

- Is a 211 MB download acceptable, or is the patched build123d worth maintaining? (`ocp-tessellate` is needed: `mesh.py` imports it. scipy and sklearn come from build123d.)
- Is a paid Developer Program membership available for the release?
