# Sprocket. The engine is a uv project under engine/, the app a SwiftPM package
# under app/, and the viewer static files served straight off disk.

ENGINE := engine

.PHONY: help test golden-update app

help:
	@echo "make test                                    run the engine test suite"
	@echo "make golden-update CASE=<name>               capture a new golden case's expected.step and expected.stl"
	@echo "make golden-update CASE=<name> APPROVED=1    replace an existing case's, which needs Daniel's decision"
	@echo "make app                                     run the Swift app"

test:
	cd $(ENGINE) && uv run pytest

# Only APPROVED=1 adds --approved. Leaving it out is what makes the refusal the
# default: see engine/tools/golden_update.py for why an existing case is locked.
golden-update:
	@test -n "$(CASE)" || { echo "usage: make golden-update CASE=<name> [APPROVED=1]"; exit 2; }
	cd $(ENGINE) && uv run python tools/golden_update.py $(CASE) $(if $(APPROVED),--approved,)

app:
	cd app && swift run SprocketApp
