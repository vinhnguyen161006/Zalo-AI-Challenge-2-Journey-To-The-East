.PHONY: install test lint typecheck smoke preflight inspect-all
install:
	python -m pip install -r requirements.lock.txt
	python -m pip install -e . --no-deps
test:
	python -m pytest -q
lint:
	python -m ruff check src tests scripts
	python -m ruff format --check src tests scripts
typecheck:
	python -m mypy src/datamate
smoke:
	datamate test-fixtures --config configs/default.yaml
preflight:
	datamate preflight --config configs/dev.yaml
inspect-all:
	datamate inspect --config configs/dev.yaml --all-businesses
