# AGENTS — Concilia /procesar API (Standalone)

API minimalista de conciliacion bancaria con un solo endpoint publico, sin base de datos, sin autenticacion, sin Celery.

## Documentacion

| Archivo | Contenido |
|---------|-----------|
| `README.md` | English — badges, API ref, arquitectura Mermaid, PDFs de prueba, deployment |
| `README.es.md` | Espanol — mismo contenido traducido |
| `docs/MATCHING_LOGICA.md` | Logica de negocio de los 5 niveles de matching |
| `docs/CATALOGO_CUENTAS.md` | Catalogo de las 19 cuentas de prueba |
| `docs/SCRIPTS.md` | Documentacion de scripts de test y conversion |
| `LICENSE` | MIT License |
| `SECURITY.md` | Politica de seguridad, API keys, Docker hardening |
| `CONTRIBUTING.md` | Guia de contribucion, setup dev, convencion commits |
| `MAINTENANCE.md` | Sync engine desde padre, parsers, Docker ops, debugging |

## Endpoint unico

| Método | Ruta | Auth | Persiste |
|--------|------|------|----------|
| `POST` | `/api/v1/conciliaciones/procesar` | No | No |

### Request (`multipart/form-data`)

| Campo | Tipo | Requerido | Ejemplo |
|-------|------|-----------|---------|
| `extracto` | PDF (binario) | Sí | archivo extracto bancario |
| `movimientos_detalle` | string (JSON array) | Sí | `[{"fecha":"01-03-2026","codigo_movimiento":"TRX001","debito":0,"credito":250000,"saldo":1750000,"conciliado":false}]` |
| `periodo` | string | No | `"202603"` (AAAAMM) |
| `cuenta_bancaria` | string (JSON object) | No | `{"numero_cuenta_bancaria":"123456","saldo_anterior_periodo":1500000,"saldo_actual_periodo":1750000}` |

### Response (`ProcesarConciliacionResponse`)

```json
{
    "estado": "completada" | "no_completada" | "error",
    "periodo": "202603",
    "movimientos_detalle": [
        {"fecha":"01-03-2026","codigo_movimiento":"TRX001","debito":0,"credito":250000,"saldo":1750000,"conciliado":true,"nota":"Conciliado con EXT-0007 (PAGO A TERCEROS AVAL) - nivel 1 (exacto)"},
        {"fecha":"02-03-2026","codigo_movimiento":"TRX002","debito":100000,"credito":0,"saldo":1650000,"conciliado":false,"nota":"No conciliado: sin contraparte en el extracto"}
    ],
    "resumen": {
        "total_movimientos": 2,
        "conciliados": 1,
        "no_conciliados": 1,
        "porcentaje_conciliacion": 50
    },
    "cuadre_diferencia": 0,
    "metricas": {"tiempo_total_ms": 120},
    "advertencias": [
        {"tipo":"saldo_anterior","mensaje":"Saldo anterior no coincide","valor_recibido":1500000,"valor_extraido":2967145201.44}
    ]
}
```

- `movimientos_detalle` devuelve el mismo array del request pero con `conciliado` actualizado (true/false) según el resultado del matching y `nota` con el mensaje de diagnóstico.
- `advertencias` puede incluir: saldo anterior/actual, cuadre_diferencia, movimientos_insuficientes, movimientos_duplicados, intereses_no_contabilizados. No detienen el flujo.

### Validaciones (422)

- Periodo debe tener formato AAAAMM.
- Período enviado debe concuerdar con el extraído del PDF. Si no → 422 con `periodo_recibido` y `periodo_extraido`.
- Cuenta enviada debe concuerdar con la extraída del PDF. Si no → 422 con `cuenta_recibida` y `cuenta_extraida`.
- `movimientos_detalle` debe ser JSON válido, array no vacío. Fechas en `dd-mm-aaaa`.
- Movimientos con `debito=0` y `credito=0` se omiten del procesamiento.

## Cómo funciona

1. Parsea el PDF con `concilia_engine/parsers/` (18 parsers regex + LLM opcional).
2. Convierte `movimientos_detalle` JSON a objetos `MovimientoContable`.
3. Ejecuta `ejecutar_pipeline_conciliacion()`: parse + matching 5 niveles + reporte.
4. Valida periodo y cuenta contra lo extraído del PDF (bloqueante).
5. Compara saldo anterior/actual recibido vs PDF (advertencia, no bloquea) + genera advertencias de proceso (cuadre_diferencia, movimientos_insuficientes, movimientos_duplicados, intereses_no_contabilizados).
6. Actualiza `conciliado` en cada movimiento según resultado del matching y genera `nota` con diagnóstico (nivel de match, candidato, motivo de no conciliación).
7. Retorna respuesta.

## Comandos

```bash
# Instalar y arrancar
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

# Docker (puerto configurable via APP_PORT / APP_HOST)
docker build -t procesar-api .
docker run -p 8000:8000 procesar-api

# Puerto custom
docker build --build-arg APP_PORT=8080 -t procesar-api .
docker run -e APP_PORT=8080 -p 8080:8080 procesar-api

# Test rapido
curl -X POST http://localhost:8000/api/v1/conciliaciones/procesar \
  -F "extracto=@extracto.pdf" \
  -F 'movimientos_detalle=[{"fecha":"01-03-2026","codigo_movimiento":"TRX001","debito":0,"credito":250000,"saldo":1750000,"conciliado":false}]'
```

## Dependencias

```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic>=2.10.0
pydantic-settings>=2.7.0
python-multipart>=0.0.18
pdfplumber>=0.11.0
pypdf>=5.1.0
```

Opcionales (LLM / VisionParser):
```
litellm>=1.50.0
markitdown>=0.1.0
PyMuPDF>=1.25.0
pyyaml>=6.0
```

## Tests

```bash
# Unitarios (18 tests, mock pipeline)
pytest tests/test_procesar.py -v -q

# E2E (5 tests, servidor real)
pytest tests/e2e/test_e2e_procesar.py -v -q

# Todos juntos
pytest tests/ -v -q
```

Estructura:
```
tests/
├── conftest.py                 ← Fixtures (client, mock_pipeline)
├── test_procesar.py            ← 18 tests unitarios (httpx + ASGITransport)
└── e2e/
    └── test_e2e_procesar.py    ← 5 tests E2E (servidor real en puerto 8002)
```

## PDFs de prueba

23 extractos reales en `tests/fixtures/reales/extractosBancarios/` (copiados del repo padre). Ver `README.md` para la tabla completa de bancos y movimientos.

19 cuentas completas (PDF + XLSX + JSON) en `tests/fixtures/reales-completas/`. Ver `docs/CATALOGO_CUENTAS.md` para el catálogo completo con parser, estado y observaciones.

## Reglas del proyecto

- `concilia_engine/` es copia del proyecto padre (`conciliacion-bancaria`) y no se modifica directamente.
- Todo el código de endpoint está en `main.py`.
- No hay base de datos. No importar SQLAlchemy, Alembic, o repositorios.
- No hay autenticación. No importar JWT, cookies, o middleware de auth.
- No hay Celery. No hay tareas asíncronas.
- `advertencias` siempre va en la respuesta aunque esté vacía (`[]`).
