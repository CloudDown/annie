.PHONY: install dev run test test-offline validate validate-subs debug-rezero clean help

UV ?= uv
ANNIE := ./annie.py

help:
	@echo "Targets:"
	@echo "  make install      uv sync + ~/.config/annie/*.toml (si absents)"
	@echo "  make dev          alias for install"
	@echo "  make run          launch interactive CLI"
	@echo "  make test         suite unitaire (offline)"
	@echo "  make test-offline scripts debug sans réseau"
	@echo "  make validate     validation réseau (10 anime)"
	@echo "  make validate-subs validation sous-titres OpenSubtitles (réseau)"
	@echo "  make survey-nyaa   collecte titres Nyaa + rapport patterns (réseau)"
	@echo "  make parsing-loop  boucle parsing top MAL → 2000 (réseau)"
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
	$(UV) run python scripts/debug_match.py --fixture
	$(UV) run python scripts/debug_subtitles.py --fixture
	$(UV) run python scripts/debug_catalog.py --offline
	$(UV) run python scripts/debug_parse.py --fixture parse_titles
	$(UV) run python scripts/debug_coherence.py --fixture catalog_coherence_uniform

validate: install
	$(UV) run python scripts/validate_franchise.py --limit 10

validate-subs: install
	$(UV) run python scripts/debug_subtitles.py --fixture --live

survey-nyaa: install
	$(UV) run python scripts/survey_nyaa_titles.py --top 40 --pages 2 --workers 4

parsing-loop: install
	$(UV) run python scripts/parsing_improve_loop.py --loop --workers 4

validate-top: install
	$(UV) run python scripts/validate_franchise.py --top 1000 --workers 2 --output scripts/results/validate_top1000.json

debug-rezero: install
	$(UV) run python scripts/debug_catalog.py --offline

clean:
	rm -rf .venv build dist *.egg-info annie.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
