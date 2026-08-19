SHELL := /usr/bin/env bash

venv::
	rm -rf venv
	python -m venv venv
	./venv/bin/pip install --upgrade pip

pip::	
	./venv/bin/pip install -e .[dev]

lint:
	./venv/bin/ruff check src tests

format:
	./venv/bin/ruff check --fix src tests
	./venv/bin/ruff format src tests

tests::
	source tests/env.tests.sh && ./venv/bin/pytest -vrP tests --cov=src/mysiar/disk_cache_data --cov-report=term-missing

clean::
	rm -rf dist build *.egg-info

build:: clean
	./venv/bin/python -m build	

upload-test::
	$(MAKE) build
	venv/bin/python -m twine upload -u $${PYPI_USER} -p $${PYPI_PASS_TEST} --verbose --repository testpypi dist/*

upload::
	$(MAKE) build
	venv/bin/python -m twine upload -u $${PYPI_USER} -p $${PYPI_PASS} --verbose dist/*
