.PHONY: install dev run test test-offline debug-rezero smoke omarchy clean help

UV ?= uv
ANNIE := ./bin/annie.py

help:
	@echo "Targets:"
	@echo "  make install      uv sync + config.toml + ~/.local/bin/annie"
	@echo "  make omarchy      native Omarchy launcher, menu, Super+Shift+A"
	@echo "  make dev          alias for install"
	@echo "  make run          launch interactive CLI"
	@echo "  make test         suite unitaire (offline)"
	@echo "  make test-offline régressions fixtures (sans réseau)"
	@echo "  make debug-rezero régression catalogue Re:Zero offline"
	@echo "  make smoke        Tanya / Re:Zero / Konosuba film (offline)"
	@echo "  make clean        remove venv and build artifacts"

install:
	$(UV) sync
	$(UV) run python -c "from annie.user_config import ensure_user_config; ensure_user_config()"
	mkdir -p $(HOME)/.local/bin
	ln -sfn $(CURDIR)/.venv/bin/annie $(HOME)/.local/bin/annie

dev: install

omarchy: install
	packaging/omarchy/install.sh

run: install
	$(ANNIE)

test: install
	$(UV) run python -m unittest discover -s tests -v

test-offline: install
	$(UV) run python -m unittest \
		tests.test_parsing \
		tests.test_stream \
		tests.test_catalog \
		tests.test_subtitles \
		tests.test_subtitle_fixtures \
		tests.test_season_coherence \
		tests.test_fixtures \
		-v

debug-rezero: install
	$(UV) run python scripts/debug_catalog.py --offline

smoke: install
	$(UV) run python -m unittest \
		tests.test_catalog.ReZeroCatalogFixtureTests \
		tests.test_catalog.TanyaAllAnimeScopedTests \
		tests.test_catalog.MovieSectionFilterTests \
		-q
	$(UV) run python scripts/debug_catalog.py --offline
	$(UV) run python scripts/smoke_catalog.py

clean:
	rm -rf .venv build dist *.egg-info annie.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
