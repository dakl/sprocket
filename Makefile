# Sprocket. The engine is a uv project under engine/, the app a SwiftPM package
# under app/, and the viewer static files served straight off disk.

ENGINE := engine

# planegcs has no macOS wheel, so a Mac builds it from source (brew install eigen
# boost). CMake then picks the Command Line Tools SDK, which can be newer than
# the linker Xcode ships, so point it at Xcode's own.
ifeq ($(shell uname),Darwin)
SOLVER_ENV := SDKROOT=$(shell xcrun --sdk macosx --show-sdk-path) CMAKE_PREFIX_PATH=/opt/homebrew
endif

# The Command Line Tools ship Swift Testing but leave its macro plugin in a
# folder the build does not search, so `swift test` there fails with "plugin for
# module 'TestingMacros' not found". A full Xcode finds it unaided.
CLT_TESTING_MACROS := /Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib
ifneq ($(wildcard $(CLT_TESTING_MACROS)),)
ifeq ($(findstring CommandLineTools,$(shell xcode-select -p 2>/dev/null)),CommandLineTools)
SWIFT_TEST_FLAGS := -Xswiftc -load-plugin-library -Xswiftc $(CLT_TESTING_MACROS)
endif
endif

.PHONY: help test test-engine test-solver test-viewer test-ui test-app golden-update app web

help:
	@echo "make test                                    engine, viewer unit and browser tests"
	@echo "make test-engine                             the engine suite, golden cases included"
	@echo "make test-solver                             the engine solver against the viewer solver (needs node, brew eigen and boost on a Mac)"
	@echo "make test-viewer                             the viewer's node unit tests"
	@echo "make test-ui                                 Playwright against a live engine, in headless Chrome"
	@echo "make test-app                                the Swift app's unit tests (macOS)"
	@echo "make golden-update CASE=<name>               capture a new golden case's expected.step and expected.stl"
	@echo "make golden-update CASE=<name> APPROVED=1    replace an existing case's, which needs Daniel's decision"
	@echo "make app                                     run the Swift app"
	@echo "make web DOC=<document.sprocket>                 serve a document to the viewer at http://127.0.0.1:8765/"

test: test-engine test-viewer test-ui $(if $(filter Darwin,$(shell uname)),test-app,)

test-engine:
	cd $(ENGINE) && uv run pytest

# The parity tests skip without the `solver` extra, so test-engine alone does not run them.
test-solver:
	cd $(ENGINE) && $(SOLVER_ENV) uv run --extra solver pytest tests/test_solver_parity.py -v

test-viewer:
	node --test viewer/test/*.test.js

test-app:
	cd app && swift test $(SWIFT_TEST_FLAGS)

# Uses the installed Chrome (see e2e/playwright.config.js), so no browser is downloaded.
test-ui:
	cd e2e && $(SOLVER_ENV) npm ci --silent && $(SOLVER_ENV) npx playwright test

# Only APPROVED=1 adds --approved. Leaving it out is what makes the refusal the
# default: see engine/tools/golden_update.py for why an existing case is locked.
golden-update:
	@test -n "$(CASE)" || { echo "usage: make golden-update CASE=<name> [APPROVED=1]"; exit 2; }
	cd $(ENGINE) && uv run python tools/golden_update.py $(CASE) $(if $(APPROVED),--approved,)

# Each open document starts its own `sprocket daemon`, which writes that
# document's part.mesh, features.json and schemas.json under ~/Library/Caches.
app:
	cd app && swift run SprocketApp

web:
	@test -n "$(DOC)" || { echo "usage: make web DOC=<document.sprocket>"; exit 2; }
	cd $(ENGINE) && uv run sprocket web $(abspath $(DOC)) --socket $$(mktemp -d)/d.sock
