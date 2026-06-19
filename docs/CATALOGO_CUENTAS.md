# Catálogo de Cuentas de Prueba

Inventario completo de las 19 cuentas de prueba en `tests/fixtures/reales-completas/`.

> **Última ejecución:** `python scripts/test_all.py` — 17/19 OK, 0 errores, 2 saltados (0 movs).

---

## Resumen Ejecutivo

| Categoría | Cantidad |
|-----------|----------|
| **Total cuentas** | 19 |
| **completada** (diferencia = 0) | 9 |
| **no_completada** (diferencia > 0) | 8 |
| **Sin movimientos** (0 movs JSON) | 2 (2891, 8379) |

---

## Tabla Maestra

| # | Cuenta | Parser | Movs JSON | Estado | Diferencia | Conciliados | % | Observaciones |
|---|--------|--------|-----------|--------|------------|-------------|---|---------------|
| 1 | 0091 | FIC | 7 | completada | 0.0 | 12/26 | 46.15% | — |
| 2 | 0515 | Banco Caja Social | 1 | completada | 0.0 | 2/2 | 100.0% | — |
| 3 | 0907 | Banco Popular | 3 | no_completada | 17,696.78 | 0/3 | 0.0% | — |
| 4 | 2291 | BBVA | 21 | no_completada | 151,940.0 | 18/40 | 45.0% | — |
| 5 | 2891 | Davivienda | 0 | SKIP | — | — | — | Sin movimientos en JSON |
| 6 | 3646 | Davivienda | 1 | completada | 0.0 | 2/2 | 100.0% | — |
| 7 | 3772 | Colpatria | 21 | no_completada | 1,160,544.0 | 30/44 | 68.18% | — |
| 8 | 6130 | Davivienda | 15 | no_completada | 5,721,461,295.72 | 10/54 | 18.52% | — |
| 9 | 759-3 | Bogotá | 114 | no_completada | 73,051,160.0 | 192/215 | 89.3% | — |
| 10 | 7930 | Bancolombia | 1 | completada | 0.0 | 2/2 | 100.0% | — |
| 11 | 8379 | Davivienda | 0 | SKIP | — | — | — | 2 PDFs, 0 movimientos en JSON |
| 12 | 8997 | Banco Agrario | 1 | completada | 0.0 | 2/2 | 100.0% | 2 PDFs de prueba |
| 13 | 9199 | Davivienda | 1 | completada | 0.0 | 2/2 | 100.0% | — |
| 14 | 9260 | Davivienda | 3 | completada | 0.0 | 0/3 | 0.0% | — |
| 15 | 9271 | Bancolombia | 3 | no_completada | 23,179,049,767.86 | 0/3 | 0.0% | — |
| 16 | 9517 | Itaú | 8 | no_completada | 2,000,000,000.0 | 8/63 | 12.7% | — |
| 17 | Cartagena | Occidente | 2 | completada | 0.0 | 0/32 | 0.0% | Solo totales match (0% conciliados) |
| 18 | Monteria | Bogotá | 926 | completada | 0.0 | 1848/1876 | 98.51% | — |
| 19 | Valledupar | BBVA | 35 | no_completada | 1,914,253,942.04 | 18/63 | 28.57% | Partidas conciliatorias históricas (2021-2023) |

---

## Cuentas por Estado

### completada (diferencia = 0)

| Cuenta | Parser | Movs JSON | Conciliados | % | Observaciones |
|--------|--------|-----------|-------------|---|---------------|
| 0091 | FIC | 7 | 12/26 | 46.15% | — |
| 0515 | Banco Caja Social | 1 | 2/2 | 100.0% | — |
| 3646 | Davivienda | 1 | 2/2 | 100.0% | — |
| 7930 | Bancolombia | 1 | 2/2 | 100.0% | — |
| 8997 | Banco Agrario | 1 | 2/2 | 100.0% | 2 PDFs |
| 9199 | Davivienda | 1 | 2/2 | 100.0% | — |
| 9260 | Davivienda | 3 | 0/3 | 0.0% | — |
| Cartagena | Occidente | 2 | 0/32 | 0.0% | Solo totales |
| Monteria | Bogotá | 926 | 1848/1876 | 98.51% | — |

### no_completada (diferencia > 0)

| Cuenta | Parser | Movs JSON | Diferencia | Conciliados | % | Observaciones |
|--------|--------|-----------|------------|-------------|---|---------------|
| 0907 | Banco Popular | 3 | 17,697 | 0/3 | 0.0% | — |
| 2291 | BBVA | 21 | 151,940 | 18/40 | 45.0% | — |
| 3772 | Colpatria | 21 | 1,160,544 | 30/44 | 68.18% | — |
| 6130 | Davivienda | 15 | 5.7B | 10/54 | 18.52% | — |
| 759-3 | Bogotá | 114 | 73M | 192/215 | 89.3% | — |
| 9271 | Bancolombia | 3 | 23.2B | 0/3 | 0.0% | — |
| 9517 | Itaú | 8 | 2B | 8/63 | 12.7% | — |
| Valledupar | BBVA | 35 | 1.9B | 18/63 | 28.57% | Partidas históricas 2021-2023 |

### Sin movimientos

| Cuenta | Parser | Observaciones |
|--------|--------|---------------|
| 2891 | Davivienda | 0 movimientos en JSON |
| 8379 | Davivienda | 2 PDFs, 0 movimientos en JSON |

---

## Cuentas por Parser

| Parser | Cuentas | completada | no_completada |
|--------|---------|------------|---------------|
| BBVA | 2291, Valledupar | 0 | 2 |
| Bogotá | 759-3, Monteria | 1 | 1 |
| Davivienda | 2891, 3646, 6130, 8379, 9199, 9260 | 3 | 2 (+1 sin movs) |
| Bancolombia | 7930, 9271 | 1 | 1 |
| Colpatria | 3772 | 0 | 1 |
| Banco Popular | 0907 | 0 | 1 |
| Banco Caja Social | 0515 | 1 | 0 |
| FIC | 0091 | 1 | 0 |
| Banco Agrario | 8997 | 1 | 0 |
| Itaú | 9517 | 0 | 1 |
| Occidente | Cartagena | 1 | 0 |

---

## Estructura de cada Carpeta

```
tests/fixtures/reales-completas/
├── 0091 MARZO 2026/
│   ├── 0091 MARZO 2026.pdf          ← Extracto bancario
│   ├── movimientos.xlsx              ← Datos contables (origen)
│   └── movimientos.json              ← Datos contables (convertido por xlsx_to_json.py)
├── Cartagena/
│   ├── extracto.pdf                  ← Extracto bancario
│   ├── resultado.pdf                 ← Reporte de conciliación (salida)
│   ├── movimientos.xlsx              ← Datos contables (origen)
│   └── movimientos.json              ← Datos contables (convertido)
└── ... (19 carpetas total)
```

---

## Uso

```bash
# Probar todas las cuentas (requiere servidor en :8000)
python scripts/test_all.py

# Probar las 3 cuentas originales (sin servidor)
python scripts/test_real.py

# Probar las 16 cuentas nuevas (requiere servidor en :8000)
python scripts/test_16.py

# Convertir todos los XLSXs a JSONs
python scripts/xlsx_to_json.py
```
