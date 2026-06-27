.PHONY: install dev run test test-offline validate validate-subs debug-rezero clean help

UV ?= uv
ANNIE := ./annie.py

help:
	@echo "Targets:"
	@echo "  make install      uv sync (venv + dépendances dont libtorrent)"
	@echo "  make dev          alias for install"
	@echo "  make run          launch interactive CLI"
	@echo "  make test         suite unitaire (offline)"
	@echo "  make test-offline scripts debug sans réseau"
	@echo "  make validate     validation réseau (10 anime)"
	@echo "  make validate-subs validation sous-titres OpenSubtitles (réseau)"
	@echo "  make debug-rezero régression catalogue Re:Zero offline"
	@echo "  make clean        remove venv and build artifacts"

install:
	$(UV) sync

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

validate: install
	$(UV) run python scripts/validate_franchise.py --limit 10

validate-subs: install
	$(UV) run python scripts/debug_subtitles.py --fixture --live

validate-top: install
	$(UV) run python scripts/validate_franchise.py --top 1000 --workers 2 --output scripts/results/validate_top1000.json

debug-rezero: install
	$(UV) run python scripts/debug_catalog.py --offline

clean:
	rm -rf .venv build dist *.egg-info annie.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
