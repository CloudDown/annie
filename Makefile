.PHONY: install dev run clean help

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
ANNIE := ./annie.py

help:
	@echo "Targets:"
	@echo "  make install   create venv + editable install"
	@echo "  make dev       alias for install"
	@echo "  make run       launch interactive CLI"
	@echo "  make clean     remove venv and build artifacts"

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip

install: $(VENV)/bin/python
	$(PIP) install -e .

dev: install

run: install
	$(ANNIE)

clean:
	rm -rf $(VENV) build dist *.egg-info annie.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
