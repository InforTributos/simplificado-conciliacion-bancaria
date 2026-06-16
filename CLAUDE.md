# CLAUDE.md — Concilia /procesar API (Standalone)

## Project

Single-endpoint FastAPI microservice for bank statement reconciliation. No database, no auth, no Celery. Receives a PDF bank statement + JSON accounting movements, returns the same movements with `conciliado` flag updated and reconciliation summary.

## Documentation

| File | Content |
|------|---------|
| `README.md` / `README.es.md` | Full docs: badges, API ref, Mermaid architecture, test PDFs, deployment |
| `LICENSE` | MIT |
| `SECURITY.md` | Security policy, API keys, Docker hardening |
| `CONTRIBUTING.md` | Dev setup, testing, commit conventions, `concilia_engine/` rule |
| `MAINTENANCE.md` | Engine sync from parent, parser updates, Docker ops, debugging |

## Code structure

```
simplificada-conciliacion-bancaria/
├── main.py              # FastAPI app + /api/v1/conciliaciones/procesar endpoint
├── concilia_engine/     # Copy from parent project — DO NOT EDIT here
│   ├── pipeline.py      # execute_pipeline(): parse → match → report
│   ├── parsers/         # 16 bank PDF parsers (regex-based) + generic + LLM + Vision
│   ├── matching/        # 5-level matching engine (Nivel 0-4)
│   ├── validacion.py    # Period & account cross-validation
│   ├── report.py        # Report generator
│   └── models.py        # Domain dataclasses (no SQLAlchemy)
├── tests/
│   ├── test_procesar.py          # 18 unit tests (mock pipeline)
│   ├── e2e/test_e2e_procesar.py  # 5 E2E tests
│   └── fixtures/reales/          # 23 real bank PDFs from parent project
├── requirements.txt     # 7 packages (fastapi, uvicorn, pydantic, pdfplumber, pypdf)
├── Dockerfile           # python:3.12-slim, configurable APP_PORT/APP_HOST via ARG/ENV
├── .env                 # LLM_API_KEY, NVIDIA_API_KEY, HF_API_KEY (optional)
└── .gitignore
```

## Request/Response

**POST** `/api/v1/conciliaciones/procesar` (multipart/form-data)

Top-level response fields: `estado`, `periodo`, `movimientos_detalle`, `resumen`, `cuadre_diferencia`, `metricas`, `advertencias`.

- `movimientos_detalle` is the same array from the request, with `conciliado` set to `true`/`false` based on matching results.
- `advertencias` compares `saldo_anterior_periodo`/`saldo_actual_periodo` vs PDF — non-blocking warnings.
- Period and account validations are blocking: mismatch → 422 with both received and extracted values.

## Rules

- **No database**: No SQLAlchemy, asyncpg, Alembic imports.
- **No auth**: No JWT, cookies, middleware.
- **No Celery**: Synchronous only.
- `concilia_engine/` is a shared copy — prefer fixing issues in the parent project and recopying.
- All endpoint logic lives in `main.py`.
- `advertencias` always present in response (empty `[]` if no warnings).

## Commands

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
docker build -t procesar-api .
docker run -p 8000:8000 procesar-api

# Custom port at build or runtime
docker build --build-arg APP_PORT=8080 -t procesar-api .
docker run -e APP_PORT=8080 -p 8080:8080 procesar-api
docker run -e APP_HOST=127.0.0.1 -e APP_PORT=3000 -p 3000:3000 procesar-api
```
