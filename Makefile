.PHONY: help install install-dev test test-cov lint format type-check clean docs build publish

help:
	@echo "Harness Framework - Development Commands"
	@echo ""
	@echo "  make install      Install package in development mode"
	@echo "  make install-dev  Install with all dev dependencies"
	@echo "  make test         Run test suite"
	@echo "  make test-cov     Run tests with coverage report"
	@echo "  make lint         Run ruff linter"
	@echo "  make format       Format code with black and ruff"
	@echo "  make type-check   Run mypy type checker"
	@echo "  make clean        Remove build artifacts"
	@echo "  make docs         Build documentation"
	@echo "  make build        Build distribution packages"
	@echo "  make publish      Publish to PyPI (requires credentials)"

install:
	pip install -e .

install-dev:
	pip install -e ".[dev,docs]"
	pre-commit install

test:
	PYTHONPATH=src python -m pytest tests/ -v

test-cov:
	PYTHONPATH=src python -m pytest tests/ --cov=harness --cov-report=term-missing --cov-report=html

lint:
	ruff check src tests
	mypy src/harness

format:
	black src tests
	ruff check --fix src tests

type-check:
	mypy src/harness

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.harness.db*" -delete

docs:
	cd docs && make html

build: clean
	python -m build

publish: build
	python -m twine upload dist/*
