# Catálogo de Cuentas de Prueba

Inventario completo de las 19 cuentas de prueba en `tests/fixtures/reales-completas/`.

---

## Resumen Ejecutivo

| Categoría | Cantidad |
|-----------|----------|
| **Total cuentas** | 19 |
| **completada** (diferencia = 0) | 11 |
| **no_completada** (diferencia > 0) | 7 |
| **Sin movimientos** (0 movs JSON) | 1 (8379) |

---

## Tabla Maestra

| # | Cuenta | Carpeta | Parser Banco | Movs Extracto | Movs JSON | Estado | Diferencia | Observaciones |
|---|--------|---------|--------------|---------------|-----------|--------|------------|---------------|
| 1 | 0091 | `0091 MARZO 2026` | FIC | — | — | completada | 0.00 | — |
| 2 | 0515 | `0515 MARZO 2026` | Banco Caja Social | — | — | completada | 0.00 | — |
| 3 | 0907 | `0907 MARZO 2026` | Banco Popular | — | — | no_completada | — | — |
| 4 | 2291 | `2291 MARZO 2026` | BBVA | — | — | no_completada | — | — |
| 5 | 2891 | `2891 MARZO 2026` | Davivienda | — | — | no_completada | — | — |
| 6 | 3646 | `3646 MARZO 2026` | Davivienda | — | — | completada | 0.00 | — |
| 7 | 3772 | `3772 MARZO 2026` | Colpatria | — | — | no_completada | — | — |
| 8 | 6130 | `6130 MARZO 2026` | Davivienda | — | — | no_completada | — | — |
| 9 | 759-3 | `759-3 MARZO 2026` | Bogotá | — | — | no_completada | — | — |
| 10 | 7930 | `7930 MARZO 2026` | Bancolombia | — | — | completada | 0.00 | — |
| 11 | 8379 | `8379 MARZO 2026` | Davivienda | 2 PDFs | 0 | — | — | Sin movimientos en JSON |
| 12 | 8997 | `8997 agrario` | Banco Agrario | — | — | completada | 0.00 | PDFs de prueba: 2 archivos |
| 13 | 9199 | `9199 MARZO 2026` | Davivienda | — | — | completada | 0.00 | — |
| 14 | 9260 | `9260 MARZO 2026` | Davivienda | — | — | completada | 0.00 | — |
| 15 | 9271 | `9271 MARZO 2026` | Bancolombia | — | — | no_completada | — | — |
| 16 | 9517 | `9517 MARZO 2026` | Itaú | — | — | no_completada | — | — |
| 17 | Cartagena | `Cartagena` | Occidente | 2 | 2 | completada | 0.00 | 0% conciliados (solo totales match) |
| 18 | Monteria | `Monteria` | Bogotá | 926 | 926 | completada | 0.00 | 98.51% conciliados (924/926) |
| 19 | Valledupar | `Valledupar` | BBVA | 35 | 35 | no_completada | — | Partidas conciliatorias históricas (2021-2023) |

> **Nota:** Los valores con "—" se obtienen ejecutando `python scripts/test_16.py` (servidor corriendo en puerto 8000).

---

## Cuentas por Estado

### completada (diferencia = 0)

| Cuenta | Carpeta | Parser | Observaciones |
|--------|---------|--------|---------------|
| 0091 | `0091 MARZO 2026` | FIC | — |
| 0515 | `0515 MARZO 2026` | Banco Caja Social | — |
| 3646 | `3646 MARZO 2026` | Davivienda | — |
| 7930 | `7930 MARZO 2026` | Bancolombia | — |
| 8997 | `8997 agrario` | Banco Agrario | 2 PDFs de prueba |
| 9199 | `9199 MARZO 2026` | Davivienda | — |
| 9260 | `9260 MARZO 2026` | Davivienda | — |
| Cartagena | `Cartagena` | Occidente | 0% conciliados (solo totales) |
| Monteria | `Monteria` | Bogotá | 98.51% conciliados |

### no_completada (diferencia > 0)

| Cuenta | Carpeta | Parser | Observaciones |
|--------|---------|--------|---------------|
| 0907 | `0907 MARZO 2026` | Banco Popular | — |
| 2291 | `2291 MARZO 2026` | BBVA | — |
| 2891 | `2891 MARZO 2026` | Davivienda | — |
| 3772 | `3772 MARZO 2026` | Colpatria | — |
| 6130 | `6130 MARZO 2026` | Davivienda | — |
| 759-3 | `759-3 MARZO 2026` | Bogotá | — |
| 9271 | `9271 MARZO 2026` | Bancolombia | — |
| 9517 | `9517 MARZO 2026` | Itaú | — |
| Valledupar | `Valledupar` | BBVA | Partidas históricas 2021-2023 |

### Sin movimientos

| Cuenta | Carpeta | Parser | Observaciones |
|--------|---------|--------|---------------|
| 8379 | `8379 MARZO 2026` | Davivienda | 2 PDFs, 0 movimientos en JSON |

---

## Cuentas por Parser

| Parser | Cuentas | Estado |
|--------|---------|--------|
| BBVA | 2291, Valledupar | 0 completada, 1 no_completada |
| Bogotá | 759-3, Monteria | 0 completada, 1 no_completada |
| Davivienda | 2891, 3646, 6130, 8379, 9199, 9260 | 3 completada, 2 no_completada, 1 sin movs |
| Bancolombia | 7930, 9271 | 1 completada, 1 no_completada |
| Colpatria | 3772 | 0 completada, 1 no_completada |
| Banco Popular | 0907 | 0 completada, 1 no_completada |
| Banco Caja Social | 0515 | 1 completada |
| FIC | 0091 | 1 completada |
| Banco Agrario | 8997 | 1 completada |
| Itaú | 9517 | 0 completada, 1 no_completada |
| Occidente | Cartagena | 1 completada |

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
# Convertir todos los XLSXs a JSONs
python scripts/xlsx_to_json.py

# Probar las 3 cuentas originales (sin servidor)
python scripts/test_real.py

# Probar las 16 cuentas nuevas (requiere servidor en :8000)
python scripts/test_16.py
```
