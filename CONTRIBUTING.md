# Contributing to Harness Framework

Thank you for your interest in contributing! This document provides guidelines for getting started.

## Development Setup

```bash
# Fork and clone
git clone https://github.com/your-org/harness-framework.git
cd harness-framework

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install in development mode
pip install -e ".[dev,docs]"

# Install pre-commit hooks
pre-commit install
```

## Project Philosophy

The Harness Framework is built on several core principles:

1. **Everything is a declared surface**: Every component (prompts, tools, memory, etc.) must be explicitly declared, versioned, and testable.
2. **Self-improvement is bounded**: Proposals are constrained to declared surfaces. Every patch has an inverse. Acceptance gates prevent uncontrolled change.
3. **Context efficiency matters**: Agents should collaborate via structured knowledge graphs, not bloated context windows.
4. **Deterministic testing**: The framework must be fully testable with MockBackend — no real LLM calls in the test suite.
5. **Governance by default**: Policy validation, acceptance gates, and promotion pipelines are not optional — they are the default path.

## Code Style

- **Black** for formatting (line length: 100)
- **Ruff** for linting
- **MyPy** for type checking
- Type hints are mandatory for all public APIs
- Docstrings required for all public classes and methods

```bash
# Before committing, run:
make format   # Auto-fix formatting
make lint     # Check for issues
make test     # Run full test suite
make type-check  # Verify types
```

## Testing

### Test Requirements

- All new code must have tests
- Tests must work with `MockBackend` — no real LLM calls
- Integration tests marked with `@pytest.mark.integration`
- Aim for >90% coverage on new code

### Running Tests

```bash
# Full suite (758 tests)
make test

# With coverage
make test-cov

# Specific test file
PYTHONPATH=src python -m pytest tests/test_knowledge_graph.py -v

# Integration tests only
PYTHONPATH=src python -m pytest tests/ -m integration -v

# Skip slow tests
PYTHONPATH=src python -m pytest tests/ -m "not slow" -v
```

### Writing Tests

```python
import pytest
from harness.tools import ReadFileTool

class TestReadFileTool:
    def test_reads_allowed_file(self, tmp_path):
        tool = ReadFileTool(allowed_paths=[str(tmp_path)])
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        result = tool.run(path=str(test_file))
        assert result == "hello"

    def test_denies_traversal(self, tmp_path):
        tool = ReadFileTool(allowed_paths=[str(tmp_path)])
        with pytest.raises(PermissionError):
            tool.run(path="/etc/passwd")
```

## Making Changes

### Branch Naming

- `feature/<surface>-<description>` — New features
- `fix/<surface>-<description>` — Bug fixes
- `patch/<surface>-<description>` — Harness patch proposals
- `docs/<description>` — Documentation updates

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(graph): add entity resolution with Jaccard similarity
fix(swarm): prevent race condition in work-stealing queue
docs: update architecture diagram
patch(policy): add privilege escalation detection for mcps surface
test(lifecycle): add approval mode resolution tests
```

### Pull Request Process

1. Ensure all tests pass (`make test`)
2. Ensure linting passes (`make lint`)
3. Update documentation if needed
4. Fill out the PR template completely
5. For harness patches, include acceptance gate results
6. Request review from maintainers

## Architecture Decisions

When proposing architectural changes, please reference:

- [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) — Full design history
- [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) — Feature mapping
- [ROADMAP.md](ROADMAP.md) — Future improvements

## Surfaces

When adding a new surface:

1. Add to `SurfaceType` enum in `core/types.py`
2. Create a `*Plugin` class in `plugins/`
3. Add validation in `PolicyEngine`
4. Add acceptance gate if the surface affects evaluation
5. Register in `__init__.py` exports
6. Add tests in `tests/test_plugins.py`
7. Document in README.md surface table

## Getting Help

- Open an issue for bugs or feature requests
- Use the harness patch proposal template for self-improvement proposals
- Check existing issues and PRs before creating new ones

## Code of Conduct

- Be respectful and constructive
- Focus on technical merit
- Welcome newcomers and help them learn
- Give credit where credit is due

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
