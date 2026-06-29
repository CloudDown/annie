.PHONY: install dev run test test-offline debug-rezero clean help

UV ?= uv
ANNIE := ./annie.py

help:
	@echo "Targets:"
	@echo "  make install      uv sync + ~/.config/annie/config.toml (si absent)"
	@echo "  make dev          alias for install"
	@echo "  make run          launch interactive CLI"
	@echo "  make test         suite unitaire (offline)"
	@echo "  make test-offline régressions fixtures (sans réseau)"
	@echo "  make debug-rezero régression catalogue Re:Zero offline"
	@echo "  make clean        remove venv and build artifacts"

install:
	$(UV) sync
	$(UV) run python -c "from annie.user_config import ensure_user_config; ensure_user_config()"

dev: install

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
	$(UV) run python -m unittest tests.test_catalog.ReZeroCatalogFixtureTests -v

clean:
	rm -rf .venv build dist *.egg-info annie.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
