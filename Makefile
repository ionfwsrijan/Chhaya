.PHONY: setup test lint serve demo gate clean aws-validate deploy

PY     := .venv/Scripts/python
PIP    := .venv/Scripts/pip
RUFF   := .venv/Scripts/ruff
MYPY   := .venv/Scripts/mypy
PYTEST := $(PY) -m pytest

help:
	@echo "make setup   create the venv and install the package + dev deps"
	@echo "make test     run the full test suite with coverage"
	@echo "make lint     ruff check + format check + mypy (strict)"
	@echo "make serve    run the local console at http://127.0.0.1:8000"
	@echo "make demo     walk the whole safety loop against synthetic weather"
	@echo "make local    shorthand for serve"
	@echo "make clean    remove caches and build artifacts"

setup:
	$(PY) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,aws]"

test:
	$(PYTEST) tests/ --cov=chhaya

lint:
	$(RUFF) format --check src/ tests/
	$(RUFF) check src/ tests/
	$(MYPY) src/chhaya

serve:
	$(PY) -m chhaya.cli serve

demo:
	$(PY) -m chhaya.cli demo

local: serve

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov .aws-sam build dist src/chhaya.egg-info local_run .chhaya

aws-validate:
	python -m pip install cfn-lint
	cfn-lint template.yaml

deploy: aws-validate
	sam build
	sam deploy --guided