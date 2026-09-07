PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: setup check backend-check frontend-check test format

setup:
	python3.12 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -r backend/requirements-dev.txt
	cd frontend-login && npm ci

check: backend-check frontend-check

backend-check:
	$(PYTHON) -m ruff check backend embeddings
	$(PYTHON) -m pytest

frontend-check:
	cd frontend-login && npm run check

test:
	$(PYTHON) -m pytest
	cd frontend-login && npm test

format:
	$(PYTHON) -m ruff format backend embeddings
	cd frontend-login && npm run format
