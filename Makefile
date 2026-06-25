.PHONY: install dev run test test-offline validate debug-rezero clean help

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
ANNIE := ./annie.py

help:
	@echo "Targets:"
	@echo "  make install      create venv + editable install"
	@echo "  make dev          alias for install"
	@echo "  make run          launch interactive CLI"
	@echo "  make test         suite unitaire (offline)"
	@echo "  make test-offline scripts debug sans réseau"
	@echo "  make validate     validation réseau (10 anime)"
	@echo "  make debug-rezero régression catalogue Re:Zero offline"
	@echo "  make clean        remove venv and build artifacts"

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip

install: $(VENV)/bin/python
	$(PIP) install -e .

dev: install

run: install
	$(ANNIE)

test: install
	$(PY) -m unittest discover -s tests -v

test-offline: install
	$(PY) scripts/debug_match.py --fixture
	$(PY) scripts/debug_catalog.py --offline
	$(PY) scripts/debug_parse.py --fixture parse_titles

validate: install
	$(PY) scripts/validate_franchise.py --limit 10

validate-top: install
	$(PY) scripts/validate_franchise.py --top 1000 --workers 2 --output scripts/results/validate_top1000.json

debug-rezero: install
	$(PY) scripts/debug_catalog.py --offline

clean:
	rm -rf $(VENV) build dist *.egg-info annie.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
