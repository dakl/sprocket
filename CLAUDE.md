# CLAUDE.md

Sprocket is a parametric CAD app: a Python engine (`engine/`), a web viewer
(`viewer/`), a Swift shell (`app/`) and Playwright tests (`e2e/`). `docs/SPEC.md`
is the design record. Read its "as built" paragraphs before you change a feature.

## Every engine op ships with its UI

A new op is not done until a person can use it by clicking in the viewer: a
toolbar button that enables on the right selection, a panel with a live preview,
Enter to commit, and a Playwright test in `e2e/tests/` that drives it. The MCP
tool and the parameter form do not count. If the control needs real design work,
leave the button disabled and say why in the PR. The rule is in `docs/SPEC.md`,
under Build order and milestones.

## Lint and types

`make lint` runs strict checks for every language, old code included, and CI
runs them on every PR: ruff (every rule) and basedpyright (`all`) for the
engine, Biome and `tsc --checkJs --strict` for the viewer and e2e, swift-format
and a Swift 6 build with warnings as errors for the app. `make format` applies
the formatters. Old code does not pass yet; new code must.

- Model a domain object as a dataclass or a pydantic model, not a `dict`. A
  `dict[str, Any]` fails basedpyright's `reportExplicitAny` on purpose. Keep
  dicts for JSON at the edge, and parse them into a model there.
- Do not switch off a rule to get a change through. A rule that is wrong for
  this repo is switched off in its config, with the reason, in its own PR.

## Tests

Send output to a file and check `$?`. A pipe into `tail` hides a failure.

- Engine: `cd engine && uv run --extra mcp pytest`
- Viewer unit tests: `make test-viewer`
- Browser tests: `make test-ui` (all), or `cd e2e && npx playwright test -g "<name>"`
- A test that waits on a rebuild uses a 15 s timeout. The CI runner is slow.
- Look at the page once in a browser before you call a UI change done.
- `make metrics` counts complexity, duplication and unused code per part. The
  numbers are sensors, not targets: never restructure code to move one. On a
  PR, CI keeps one comment starting `<!-- sprocket-metrics -->` with the table
  and a `metrics.json` block holding `{head, base}`. Read it with
  `gh pr view <n> --json comments`, and explain in the PR any number that got
  worse.

## Releasing

Run the release workflow on main. It raises the engine version, commits it to
main, tags it and publishes the wheel, the examples and the DMG:

```sh
gh workflow run release --ref main -f bump=patch
```

`bump` is `patch`, `minor` or `major`. Do not raise the version or push a tag
by hand. To build an existing release's DMG again, pass `-f release=v0.10.0`.
