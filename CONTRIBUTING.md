# Contributing

Thank you for your interest in contributing! This guide will help you get started.

---

## Code of Conduct

Please be respectful and constructive in all interactions. We strive to maintain a welcoming environment for everyone.

---

## Before You Start

### Important: The `concilia_engine/` Rule

`concilia_engine/` is a **shared copy** from the parent project (`conciliacion-bancaria`). You **must not** edit files under `concilia_engine/` in this repository.

- **Parsers, matching engine, normalizer, pipeline** — all changes go to the parent project first.
- **After changes are made in the parent**, copy the updated `concilia_engine/` directory here and verify with tests.
- The only code you should modify directly in this project is `main.py`, tests, and documentation.

### What You Can Change Here

- `main.py` — the FastAPI application (endpoint logic, request/response handling)
- `tests/` — unit and E2E tests
- Configuration files: `requirements.txt`, `Dockerfile`, `.env.example`, `pyproject.toml`, `.dockerignore`, `.gitignore`
- Documentation: `README*.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `MAINTENANCE.md`

---

## Development Setup

### Prerequisites

- Python 3.12+
- Git

### Setup

```bash
git clone <repo-url>
cd simplificada-conciliacion-bancaria
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
pip install pytest pytest-cov httpx
```

### Run Locally

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The `--reload` flag enables auto-restart on code changes.

---

## Testing Requirements

**All changes must pass existing tests.** New features should include tests.

```bash
# Run all tests
pytest tests/ -q

# Run with verbose output
pytest tests/ -v

# Run only unit tests
pytest tests/test_procesar.py -q

# Run only E2E tests (requires server on port 8002)
pytest tests/e2e/ -q

# Run with coverage
pytest tests/ --cov=main --cov=concilia_engine --cov-report=term
```

### Test Conventions

- **Unit tests** (`tests/test_procesar.py`): Mock the pipeline via `mock_pipeline` fixture. Test request validation, error handling, response structure. No external dependencies.
- **E2E tests** (`tests/e2e/test_e2e_procesar.py`): Test against a real uvicorn server on port 8002. Requires the server to be running.
- Use the `client` fixture (httpx ASGITransport) for unit tests — it wraps the FastAPI app without starting a real server.
- Use `@pytest.mark.parametrize` for testing multiple input variations.

---

## How to Contribute

### 1. Find or Create an Issue

- Check existing issues before creating a new one.
- For bugs, include: steps to reproduce, expected vs actual behavior, environment details.
- For features, describe the use case and expected behavior.

### 2. Create a Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

### 3. Make Changes

- Follow existing code style:
  - Type hints on all function signatures
  - Pydantic models for request/response validation
  - No comments unless absolutely necessary
  - Match the existing error handling patterns (structured error dicts, not raw exceptions)
- If changing `main.py` response structure, update both unit and E2E tests.

### 4. Run Tests

```bash
pytest tests/ -q
```

All 23 tests must pass before submitting.

### 5. Commit

Write clear, concise commit messages:

```
feat: add X feature
fix: resolve issue with Y
docs: update API reference
test: add test for Z scenario
```

### 6. Submit a Pull Request

- Reference the issue number in the PR description.
- Describe what was changed and why.
- Note any breaking changes to the API response schema.

---

## Adding a New Parser

If you need to add a new bank parser:

1. **Do it in the parent project** (`conciliacion-bancaria`):
   - Create the parser file under `concilia_engine/parsers/`
   - Extend `BankParser` ABC
   - Implement `puede_parsear()`, `parsear()`, `extraer_info()`
   - Register it in `router.py`'s `_default_parsers()`
   - Add tests in `tests/packages/`
   - Add the real PDF fixture in `tests/fixtures/reales/extractosBancarios/`

2. **Copy to this project**:
   ```bash
   cp -r ../conciliacion-bancaria/concilia_engine/parsers/your_parser.py concilia_engine/parsers/
   cp ../conciliacion-bancaria/concilia_engine/parsers/router.py concilia_engine/parsers/
   ```

3. **Run tests** to verify no regressions:
   ```bash
   pytest tests/ -q
   ```

---

## Code Style

- **Python 3.12+** features are allowed.
- Use `pydantic` v2 models for all request/response schemas.
- Use `pydantic-settings` for environment configuration.
- All API endpoints use `multipart/form-data` with proper validation.
- Error responses use structured dicts, not raw exceptions.
- Avoid comments in code unless they explain non-obvious intent.

---

## Commit Conventions

We follow conventional commits:

| Prefix | Use for |
|--------|---------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `docs:` | Documentation only |
| `test:` | Tests only |
| `refactor:` | Code restructuring without behavior change |
| `chore:` | Build, CI, dependencies |
| `perf:` | Performance improvements |

---

## Questions?

If you have questions about contributing, open an issue or contact the maintainers.
