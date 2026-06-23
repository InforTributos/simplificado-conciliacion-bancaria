# Scripts de Prueba y Conversión

Documentación de los scripts en `scripts/`.

---

## `xlsx_to_json.py` — Conversor XLSX → JSON

Convierte los archivos `movimientos.xlsx` de cada carpeta en `tests/fixtures/reales-completas/` a `movimientos.json` con la estructura esperada por la API.

### Uso

```bash
python scripts/xlsx_to_json.py
```

### Qué hace

1. Busca `movimientos.xlsx` en cada subcarpeta de `tests/fixtures/reales-completas/`
2. Auto-detecta la fila de encabezados (busca `TIPO` o `CODIGO` en columna A)
3. Extrae columnas: `fecha`, `codigo_movimiento`, `debito`, `credito`, `saldo`
4. Salta filas de fin de datos (`SUMAS IGUALES`, `SALDO FINAL`, `GENERADO POR`, etc.)
5. Genera `movimientos.json` con la estructura:

```json
[
  {
    "fecha": "01-03-2026",
    "codigo_movimiento": "TRX001",
    "debito": 0,
    "credito": 250000,
    "saldo": 1750000,
    "conciliado": false
  }
]
```

### Configuración

Las carpetas a procesar se definen en la variable `FOLDERS` al inicio del archivo. Por defecto son `["Cartagena", "Monteria", "Valledupar"]`. Para procesar las 16 cuentas nuevas, agregar los nombres de carpeta a la lista.

---

## `test_real.py` — Pruebas de las 3 cuentas originales

Prueba las 3 cuentas del proyecto original (Cartagena, Monteria, Valledupar) usando `httpx.ASGITransport`. **No necesita servidor corriendo.**

### Uso

```bash
# Todas las cuentas
python scripts/test_real.py

# Una cuenta específica
python scripts/test_real.py Cartagena
python scripts/test_real.py Monteria
python scripts/test_real.py Valledupar
```

### Qué muestra

- Estado de la conciliación (completada / no_completada)
- Diferencia de cuadre
- Resumen (total, conciliados, no conciliados, porcentaje)
- Tiempo de procesamiento
- Advertencias (si las hay)
- Nota de diagnóstico por movimiento (conciliado / no conciliado + razón)
- Muestra de los primeros 5 movimientos conciliados y no conciliados

### Requisitos

- `httpx` instalado
- Archivos `movimientos.json` y `extracto.pdf` en cada carpeta

---

## `test_16.py` — Pruebas de las 16 cuentas nuevas

Prueba las 16 cuentas nuevas contra el API corriendo en `localhost:8000`. **Necesita servidor activo.**

### Uso

```bash
# 1. Arrancar el servidor
uvicorn main:app --host 0.0.0.0 --port 8000

# 2. Ejecutar las pruebas
python scripts/test_16.py
```

### Qué hace

1. Lee el XLSX de cada carpeta (extrae cuenta bancaria y movimientos)
2. Envía POST multipart al API con: PDF + JSON + periodo `"202603"` + cuenta bancaria
3. Muestra resultados por cuenta: estado, diferencia, conciliados/total, porcentaje, tiempo
4. Imprime resumen final: OK/errores/saltados, completada vs no_completada

### Notas

- Las 3 cuentas originales (Cartagena, Monteria, Valledupar) se excluyen automáticamente
- Si todas las carpetas ya tienen `movimientos.json`, prueba todas
- Timeout: 120 segundos por request
