# Maintenance Guide

This guide covers routine maintenance tasks for the Simplicado Conciliacion Bancaria project.

---

## Table of Contents

- [Syncing `concilia_engine/` from the Parent](#syncing-concilia_engine-from-the-parent)
- [Updating Parsers](#updating-parsers)
- [Test PDFs](#test-pdfs)
- [Running Tests](#running-tests)
- [Docker Maintenance](#docker-maintenance)
- [Dependencies](#dependencies)
- [Environment Variables](#environment-variables)
- [Common Issues](#common-issues)

---

## Syncing `concilia_engine/` from the Parent

`concilia_engine/` is a copy from the parent project at `C:\PROYECTOS\IA\conciliacion-bancaria\concilia_engine/`. When the parent is updated, sync it here:

```bash
# From the child project root
cp -r ../conciliacion-bancaria/concilia_engine/* concilia_engine/

# Verify no regressions
pytest tests/ -q
```

**Always run tests after syncing.** Changes in the parent's engine (parsers, matching, models) can break this project's response structure.

### What Gets Synced

| Directory / File | Contains |
|------------------|----------|
| `config.py` | MatchConfig, ParseConfig, LLMConfig |
| `models.py` | Domain dataclasses |
| `normalizer.py` | Date/amount/description utilities |
| `pipeline.py` | `ejecutar_pipeline_conciliacion()` |
| `report.py` | `generar_informe()` |
| `validacion.py` | Period & account cross-validation |
| `matching/` | 5-level reconciliation engine |
| `parsers/` | 16 bank parsers + generic + LLM + Vision + Excel |
| `prompts/` | YAML prompt templates for LLM |
| `utils/` | Shared utilities |

### What Does NOT Sync

- `main.py` — this is unique to the child project
- `tests/` — child project has its own tests
- Configuration files — `.env`, `Dockerfile`, etc.

---

## Updating Parsers

### Adding a New Bank Parser

1. **Parent project**:
   - Create `concilia_engine/parsers/nuevo_banco.py` extending `BankParser`
   - Register in `concilia_engine/parsers/router.py` (`_default_parsers()`)
   - Add tests and real PDF fixtures
   - Run parent tests: `pytest tests/packages/ -q`

2. **Child project**:
   ```bash
   cp ../conciliacion-bancaria/concilia_engine/parsers/nuevo_banco.py concilia_engine/parsers/
   cp ../conciliacion-bancaria/concilia_engine/parsers/router.py concilia_engine/parsers/
   pytest tests/ -q
   ```

### Fixing an Existing Parser

1. **Parent project** — make the fix in the parser file
2. **Child project** — copy the updated file:
   ```bash
   cp ../conciliacion-bancaria/concilia_engine/parsers/parser_a_fixear.py concilia_engine/parsers/
   pytest tests/ -q
   ```

### Parser Debugging Checklist

When a parser fails to extract movements from a PDF:

1. Check `pdfplumber` output: `import pdfplumber; pdfplumber.open("file.pdf").pages[0].extract_text()`
2. Verify bank detection regex in `puede_parsear()` matches the PDF header
3. Verify movement regex handles line concatenation (use `re.finditer()`, not `re.match()`)
4. Check for character encoding issues (common in typewriter-layout PDFs like Banco Popular)
5. Check for character doubling (AV Villas PDFs)
6. For scanned PDFs: verify `NVIDIA_API_KEY` is set and VisionParser is enabled

---

## Test PDFs

The parent project has 23 real bank statement PDFs at:

```
C:\PROYECTOS\IA\conciliacion-bancaria\tests\fixtures\reales\extractosBancarios/
```

### Copy to Child Project

```bash
mkdir -p tests/fixtures/reales/extractosBancarios
cp ../conciliacion-bancaria/tests/fixtures/reales/extractosBancarios/*.pdf tests/fixtures/reales/extractosBancarios/
```

### PDF Summary

| Parser | PDF Count | Total Movs | Scanned? |
|--------|-----------|------------|----------|
| BBVA | 2 | 28 | bbva2 requires VisionParser |
| Davivienda | 2 | 327 | No |
| Bancolombia | 1 | 42 | No |
| Bogotá | 2 | 953 | No |
| Occidente | 2 | 61 | No |
| Serfinanza | 1 | 2 | No |
| Banco GNB | 1 | 35 | No |
| Banco Popular | 2 | 35 | bancoPopular2 requires VisionParser |
| Bancoomeva | 1 | 21 | No |
| AV Villas | 1 | 1 | No |
| FIC | 1 | 62 | No |
| Colpatria | 1 | 23 | No |
| Banco Caja Social | 1 | 1 | No |
| Itaú | 1 | 55 | No |
| Davibanck | 2 | 0 | No (saldos only) |
| Banco Agrario | 2 | 0 | No (saldos only) |
| **Total** | **23** | **1,647+** | **2 require VisionParser** |

---

## Running Tests

### All Tests

```bash
pytest tests/ -q
```

### Unit Tests Only

```bash
pytest tests/test_procesar.py -q -v
```

### E2E Tests Only

```bash
# Start server first
uvicorn main:app --port 8002 &
# Run E2E tests
pytest tests/e2e/ -q -v
```

### Coverage Report

```bash
pytest tests/ --cov=main --cov=concilia_engine --cov-report=html
# Open htmlcov/index.html in browser
```

### Expected Test Results

- **23 tests total** — all should pass
- 18 unit tests: mock pipeline, test all response paths (success, validation errors, pipeline errors, warnings)
- 5 E2E tests: server accessibility, input validation, basic processing

---

## Docker Maintenance

### Rebuild Image

```bash
docker build -t procesar-api:latest .

# With custom default port
docker build --build-arg APP_PORT=8080 -t procesar-api:latest .
```

### Update Running Container

```bash
docker stop procesar-api
docker rm procesar-api
docker build -t procesar-api:latest .
docker run -d --name procesar-api -p 8000:8000 --restart unless-stopped procesar-api:latest

# Custom port / host / network
docker run -d --name procesar-api -e APP_PORT=8080 -p 8080:8080 --restart unless-stopped procesar-api:latest
docker run -d --name procesar-api -e APP_HOST=127.0.0.1 -e APP_PORT=3000 -p 3000:3000 procesar-api:latest
```

### Configurable Port and Host

The Dockerfile accepts build args and runtime env vars:

| Variable | Default | Usage |
|----------|---------|-------|
| `APP_PORT` | `8000` | Runtime: `docker run -e APP_PORT=8080 ...`<br/>Build: `docker build --build-arg APP_PORT=8080 ...` |
| `APP_HOST` | `0.0.0.0` | Runtime: `docker run -e APP_HOST=127.0.0.1 ...`<br/>Build: `docker build --build-arg APP_HOST=127.0.0.1 ...` |

The `EXPOSE` and `HEALTHCHECK` directives also use `$APP_PORT`.

### Check Health

```bash
docker ps --filter name=procesar-api --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

The container should show `(healthy)` in the status.

### Check Logs

```bash
docker logs procesar-api
```

### Clean Up

```bash
# Remove unused images and build cache
docker image prune -f
docker builder prune -f
```

---

## Dependencies

### Core (Always Required)

| Package | Min Version | Purpose |
|---------|-------------|---------|
| `fastapi` | 0.115.0 | Web framework |
| `uvicorn` | 0.32.0 | ASGI server |
| `pydantic` | 2.10.0 | Data validation |
| `pydantic-settings` | 2.7.0 | Env var management |
| `python-multipart` | 0.0.18 | Form data parsing |
| `pdfplumber` | 0.11.0 | PDF text extraction |
| `pypdf` | 5.1.0 | PDF parsing |

### Optional (LLM Features)

| Package | Min Version | Purpose |
|---------|-------------|---------|
| `litellm` | 1.60.0 | LLM provider abstraction |
| `markitdown` | 0.1.0 | PDF to markdown conversion |
| `PyMuPDF` | 1.25.0 | PDF to image rendering (VisionParser) |
| `pyyaml` | 6.0.0 | YAML prompt loading |

To enable LLM features, uncomment these in `requirements.txt` and install:

```bash
pip install litellm>=1.60.0 markitdown>=0.1.0 PyMuPDF>=1.25.0 pyyaml>=6.0.0
```

### Security Auditing

```bash
pip install pip-audit
pip-audit
```

---

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MAX_FILE_SIZE_MB` | No | `50` | Max PDF upload size in MB |
| `APP_PORT` | No | `8000` | Uvicorn listen port (Docker only) |
| `APP_HOST` | No | `0.0.0.0` | Uvicorn bind address (Docker only) |
| `LLM_API_KEY` | No | — | Generic LLM API key (LiteLLM) |
| `LLM_MODEL` | No | `gemini/gemini-2.0-flash` | Primary LLM model |
| `NVIDIA_API_KEY` | No | — | NVIDIA NIM API key (VL models) |
| `GEMINI_API_KEY` | No | — | Gemini-specific API key |
| `HF_API_KEY` | No | — | Hugging Face API key |
| `LLM_BACKUP_MODEL` | No | `openai/gpt-4o-mini` | First backup model |
| `LLM_ORCHESTRATOR_MODEL` | No | `gemini/gemini-2.0-flash` | Orchestrator model |
| `LLM_VISION_MODEL` | No | `nvidia_nim/nvidia/llama-3.1-nemotron-nano-vl-8b-v1` | VL model for scanned PDFs |

---

## Common Issues

### "ERR_PDF_SOLO_IMAGEN" Error

The PDF is scanned/image-only. Solutions:
1. Set `NVIDIA_API_KEY` in `.env` to enable VisionParser
2. Or use a text-based PDF version from the bank

### "ERR_NO_ES_EXTRACTO" Error

The PDF has no extractable movements and no recognizable banking keywords. Solutions:
1. Verify the file is actually a bank statement PDF
2. Try a different PDF viewer/export from the bank's portal
3. Add a new parser for the bank in the parent project

### Parser Returns 0 Movements for Known Bank

1. Check `pdfplumber` text extraction quality with:
   ```python
   import pdfplumber
   with pdfplumber.open("file.pdf") as pdf:
       for page in pdf.pages:
           print(page.extract_text())
   ```
2. Check if the bank's PDF format has changed (banks occasionally update layouts)
3. Verify the regex patterns in the parser file match the new format

### Docker Container Not Healthy

```bash
# Check logs
docker logs procesar-api

# Common causes:
# 1. Missing .env file or API keys
# 2. Port conflict (8000 already in use) — use APP_PORT to change
# 3. Dependency installation failed during build
```

### Tests Fail After Engine Sync

If tests fail after copying `concilia_engine/` from the parent:

1. Check for new model fields in `models.py` that might break response serialization
2. Check for new required parameters in `ejecutar_pipeline_conciliacion()`
3. Update `main.py` response construction logic to match any new engine output format
4. Update test fixtures in `tests/conftest.py` if mock pipeline output format changed

---

## Version History

Track engine syncs here:

| Date | Description | Tests |
|------|-------------|-------|
| — | Initial copy from parent | 23/23 passing |
