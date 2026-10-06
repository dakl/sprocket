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

## Tests

Send output to a file and check `$?`. A pipe into `tail` hides a failure.

- Engine: `cd engine && uv run --extra mcp pytest`
- Viewer unit tests: `make test-viewer`
- Browser tests: `make test-ui` (all), or `cd e2e && npx playwright test -g "<name>"`
- A test that waits on a rebuild uses a 15 s timeout. The CI runner is slow.
- Look at the page once in a browser before you call a UI change done.
