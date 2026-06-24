# Lógica de Negocio del Motor de Conciliación Bancaria

## Visión General

El motor de conciliación bancaria (`concilia_engine/matching/`) compara **movimientos del extracto bancario** (PDF) contra **movimientos contables** (JSON/Excel) para determinar cuáles están conciliados. El proceso es **unidireccional**: cada movimiento del extracto se busca en la contabilidad, nunca al revés.

Los movimientos contables llegan con `conciliado: false` y el motor los marca como `true` o `false` según el resultado del matching.

---

## Estructura de Datos

### Extracto Bancario (`MovimientoExtracto`)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `fecha` | date | Fecha del movimiento en el extracto |
| `descripcion` | str | Descripción del movimiento |
| `debito` | float | Monto del débito (si aplica) |
| `credito` | float | Monto del crédito (si aplica) |
| `saldo` | float | Saldo después del movimiento |
| `naturaleza` | str | `"debito"` o `"credito"` |

### Contabilidad (`MovimientoContable`)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `fecha` | date | Fecha del movimiento contable |
| `descripcion` | str | Descripción del movimiento |
| `valor` | float | Monto del movimiento (positivo siempre) |
| `naturaleza` | str | `"debito"` o `"credito"` |
| `naturaleza_matching` | str | Naturaleza invertida para matching (se asigna en Nivel 0) |
| `codigo_movimiento` | str | Código del sistema contable (**NO se usa para matching**) |
| `codigo_comprobante` | str \| None | Código único del comprobante contable (`codig_cp_contable` en el request). Se usa para identificar pares de reversión. |
| `cons_cp_contable` | str \| None | Si tiene valor, este movimiento es una reversión. Apunta al `codigo_comprobante` del movimiento original que anula. Se excluye del matching contra el extracto. |
| `conciliado` | bool | Estado de conciliación (se actualiza al final) |

### Clave de Matching

El matching se basa en **fecha + valor + naturaleza**. El `codigo_movimiento` es un identificador del sistema contable interno y **no existe en los extractos bancarios**, por lo que no se utiliza para matching.

---

## Los 5 Niveles del Motor de Matching

### Nivel 0: Inversión de Naturaleza (Preparación)

**Archivo:** `concilia_engine/matching/nivel0.py`

**Por qué existe:**

En contabilidad de doble entrada para cuentas de activo (PUC clase 1), el registro contable tiene la naturaleza **invertida** respecto al extracto bancario:

| Operación | Extracto Bancario | Contabilidad (Libros) |
|-----------|-------------------|------------------------|
| Consignación | **Crédito** (aumenta saldo) | **Débito** (registra entrada) |
| Retiro/Cheque | **Débito** (disminuye saldo) | **Crédito** (registra salida) |

**Qué hace:**

Invierte la naturaleza de los movimientos contables para que `naturaleza_matching` coincida con la naturaleza del extracto. Si un movimiento contable es `debito`, su `naturaleza_matching` será `credito`, y viceversa.

**Configuración:** Se controla con `config.invertir_naturaleza` (default: `True`).

**Ejemplo:**

```
Contabilidad:  "Depósito proveedor" → naturaleza: "debito"
Post-inversión: naturaleza_matching: "credito"
Extracto:       "CONSIGNACION" → naturaleza: "credito"
→ Match posible (ambos "credito")
```

---

### Nivel 1: Match Exacto (Fecha + Valor + Naturaleza)

**Archivo:** `concilia_engine/matching/nivel1.py`

**Criterio de matching:**

| Condición | Tolerancia |
|-----------|------------|
| `fecha_contabilidad == fecha_extracto` | Exacta (0 días) |
| `abs(valor_contabilidad - valor_extracto) <= tolerancia` | Default: $0.01 |
| `naturaleza_matching == naturaleza_extracto` | Exacta |

**Confianza:**
- **0.95** — candidato único encontrado
- **0.85** — múltiples candidatos, desambiguados por similitud de descripción

**Algoritmo:**

1. Para cada movimiento del extracto, buscar en la contabilidad:
   - Mismo valor (dentro de tolerancia)
   - Misma naturaleza (post-inversión)
   - Misma fecha
2. Si hay **1 candidato** → match directo
3. Si hay **más de 1 candidato** → seleccionar el de mayor similitud de descripción
4. Si hay **0 candidatos** → pasa al Nivel 2

**Ejemplo:**

```
Extracto:  01-03-2026 | $250,000 | credito | "CONSIGNACION"
Contabilidad: 01-03-2026 | $250,000 | credito (post-inversión) | "Depósito cliente XYZ"
→ Match exacto, confianza 0.95
```

---

### Nivel 2: Match por Fecha Flexible

**Archivo:** `concilia_engine/matching/nivel2.py`

**Por qué existe:**

Muchas veces el banco registra un movimiento 1-3 días después de que la empresa lo registra en contabilidad. Por ejemplo, un cheque emitido el 01/03 puede clearar el 03/03.

**Criterio de matching:**

| Condición | Tolerancia |
|-----------|------------|
| `abs(valor_contabilidad - valor_extracto) <= tolerancia` | Default: $0.01 |
| `naturaleza_matching == naturaleza_extracto` | Exacta |
| `abs(fecha_contabilidad - fecha_extracto) <= max_dias` | Default: 5 días |
| `fecha_contabilidad != fecha_extracto` | Se excluye el día 0 (ya cubierto por Nivel 1) |

**Confianza:**

```
confianza = max(0.10, 0.90 - (0.05 × días_diferencia))
```

| Días de diferencia | Confianza |
|---------------------|-----------|
| 1 día | 0.85 |
| 2 días | 0.80 |
| 3 días | 0.75 |
| 4 días | 0.70 |
| 5 días | 0.65 |

**Algoritmo:**

1. Para cada movimiento del extracto no conciliado:
   - Buscar candidatos con mismo valor + naturaleza + fecha dentro de ventana
2. Si hay candidatos → ordenar por: (menor días_diferencia, mayor similitud de descripción)
3. Seleccionar el mejor candidato
4. Si no hay candidatos → pasa al Nivel 3

**Ejemplo:**

```
Extracto:     03-03-2026 | $150,000 | debito | "PAGO PROVEEDOR ABC"
Contabilidad: 01-03-2026 | $150,000 | debito (post-inversión) | "Cheque #1234Proveedor"
→ Match por fecha flexible, 2 días diferencia, confianza 0.80
```

---

### Nivel 3: Match Grupal N:M (Subset-Sum)

**Archivo:** `concilia_engine/matching/nivel3.py`

**Por qué existe:**

En la vida real, los movimientos no siempre son 1:1. Ejemplos:

- **1 Extracto ↔ N Contabilidad:** Un depósito de $500,000 en el extracto puede ser la suma de 3 cheques individuales registrados en contabilidad ($200K + $150K + $150K).
- **N Extracto ↔ 1 Contabilidad:** Varios retiros pequeños en el extracto pueden corresponder a un solo registro contable consolidado.
- **N Extracto ↔ M Contabilidad:** Grupo de movimientos similares que se cancelan entre sí.

**Las 3 Direcciones:**

#### Dirección A: 1 Extracto ↔ N Contabilidad

```
Extracto:     $500,000 | credito | "DEPOSITO GRupal"
Contabilidad: $200,000 | credito | "Cheque cliente A"
              $150,000 | credito | "Cheque cliente B"
              $150,000 | credito | "Cheque cliente C"
→ 1 extracto concilia con 3 movimientos contables (200K + 150K + 150K = 500K)
```

#### Dirección B: N Extracto ↔ 1 Contabilidad

```
Extracto:     $50,000  | debito | "COMISION BANCARIA"
              $30,000  | debito | "COMISION BANCARIA"
              $20,000  | debito | "COMISION BANCARIA"
Contabilidad: $100,000 | debito (post-inversión) | "Total comisiones marzo"
→ 3 extractos concilian con 1 movimiento contable (50K + 30K + 20K = 100K)
```

#### Dirección C: N Extracto ↔ M Contabilidad (por descripción)

```
Extracto:     Grupo "PROVEEDOR XYZ" → $300,000 total
Contabilidad: Grupo "PROVEEDOR XYZ" → $300,000 total
→ Match grupal por descripción similar + suma igual
```

**Algoritmo (Subset-Sum con Backtracking):**

1. Filtrar candidatos por: misma naturaleza + fecha dentro de ventana
2. Ordenar por valor descendente (para mejor poda)
3. Buscar subconjuntos cuya suma esté dentro de tolerancia del valor objetivo
4. Límite: máximo 10 subconjuntos encontrados (prevenir explosión combinatoria)
5. Seleccionar el subconjunto con **menor cantidad de elementos** (preferir simplicidad)
6. Si no hay subconjuntos → pasa al Nivel 4

**Cálculo de confianza:**

```
base = 0.70
+ bonus_similitud_descripcion (si promedio >= 0.3): +0.10
- penalidad_tamaño: max(0, (elementos - 3)) × 0.02
```

| Tamaño del grupo | Confianza base |
|------------------|----------------|
| 1-3 elementos | 0.70 - 0.80 |
| 4-5 elementos | 0.68 - 0.78 |
| 6+ elementos | 0.66 - 0.76 |

**Límites de seguridad:**
- `max_grupo_items`: máximo elementos por grupo (default: 20)
- `max_dias_diferencia`: ventana de fechas para candidatos del grupo

---

### Nivel 4: Clasificación de No Conciliados + Fórmula de Cuadre

**Archivo:** `concilia_engine/matching/nivel4.py`

**Qué hace:**

Los movimientos que no pudieron conciliarse en los niveles anteriores se clasifican:

| Categoría | Lado | Descripción |
|-----------|------|-------------|
| **Cheques no cobrados** | Contabilidad | Débitos en libros que no aparecen en el extracto (cheques emitidos pero no presentados al banco) |
| **Consignaciones en tránsito** | Contabilidad | Créditos en libros que no aparecen en el extracto (depósitos registrados pero no procesados por el banco) |
| **Partidas del extracto** | Extracto | Movimientos del banco que no se encontraron en contabilidad |
| **Partidas de libros** | Contabilidad | Movimientos contables que no se encontraron en el extracto |

**Fórmula de Cuadre (Balance):**

```
Saldo Libros + Partidas Extracto = Saldo Extracto + Partidas Libros
```

O expresado de otra forma:

```
Saldo Libros + Partidas Extracto = Saldo Extracto + Cheques No Cobrados + Consignaciones Tránsito
```

Donde:
- `Saldo Libros` = último saldo registrado en la contabilidad del período
- `Saldo Extracto` = saldo final del extracto bancario
- `Partidas Extracto` = suma de movimientos no conciliados del extracto
- `Partidas Libros` = suma de movimientos no conciliados de la contabilidad

**Cálculo de diferencia:**

```python
suma_iguales_libros = saldo_libros + partidas_extracto
suma_iguales_extracto = saldo_extracto + partidas_libros
diferencia = abs(suma_iguales_libros - suma_iguales_extracto)
```

**Interpretación de `diferencia`:**

| Valor | Significado |
|-------|-------------|
| `0.00` | Cuadre perfecto — la fórmula de balance se cumple |
| `> 0.00` | Diferencia estructural — los datos no cuadran (datos faltantes, duplicados, o errores) |

**Importante:** La `diferencia` es **independiente** del porcentaje de conciliación. Se puede tener 100% de movimientos conciliados pero diferencia ≠ 0 si los saldos iniciales/finales no coinciden con los movimientos.

---

## Flujo Completo del Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    PIPELINE DE CONCILIACIÓN                   │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  1. PARSEO DEL PDF                                           │
│     Extracto PDF → ParserRouter → MovimientoExtracto[]        │
│     + InfoExtracto (saldo_anterior, saldo_final, periodo)    │
│                                                               │
│  2. PARSEO DEL JSON/EXCEL                                    │
│     movimientos_detalle JSON → MovimientoContable[]           │
│     (cada movimiento tiene conciliado: false)                 │
│                                                               │
│  3. MATCHING (5 niveles)                                      │
│     ┌──────────────────────────────────────────────────┐     │
│     │ Nivel 0: Invertir naturaleza contable             │     │
│     │ Nivel 1: Match exacto (fecha + valor + naturaleza)│     │
│     │ Nivel 2: Match fecha flexible (ventana N días)    │     │
│     │ Nivel 3: Match grupal N:M (subset-sum)            │     │
│     │ Nivel 4: Clasificar no conciliados + cuadre       │     │
│     └──────────────────────────────────────────────────┘     │
│                                                               │
│  4. ACTUALIZACIÓN DE ESTADOS                                  │
│     Cada MovimientoContable.matched → conciliado: true        │
│     Movimientos no encontrados → conciliado: false            │
│     Se genera nota con diagnóstico por cada movimiento        │
│                                                               │
│  5. RESPUESTA                                                │
│     movimientos_detalle con conciliado y nota actualizados    │
│     + resumen (totales, porcentaje)                           │
│     + cuadre_diferencia                                       │
│     + advertencias (saldo anterior/actual, cuadre,            │
│       movimientos insuficientes/duplicados,                   │
│       intereses no contabilizados)                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Configuración del Motor

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `tolerancia_monto` | 0.01 | Diferencia máxima aceptada entre montos |
| `max_dias_diferencia` | 5 | Ventana de días para match flexible y grupal |
| `invertir_naturaleza` | True | Invertir débito↔credito en contabilidad |
| `max_grupo_items` | 20 | Máximo elementos por grupo en Nivel 3 |
| `forzar_llm` | False | Forzar parsing con LLM cuando regex falla |

---

## Ejemplo Práctico: Conciliación de Cartagena (Marzo 2026)

### Datos de entrada

- **Extracto:** 1,992 movimientos, saldo anterior $1,500,000, saldo actual $1,750,000
- **Contabilidad:** 1,992 movimientos, saldo libros $1,750,000

### Proceso

1. **Nivel 0:** Invertir naturaleza de 1,992 movimientos contables
2. **Nivel 1:** Buscar matches exactos (misma fecha + valor + naturaleza)
   - Resultado: ~1,800 matches (confianza 0.95)
3. **Nivel 2:** Para los ~192 restantes, buscar con fecha flexible (±5 días)
   - Resultado: ~150 matches (confianza 0.70-0.85)
4. **Nivel 3:** Para los ~42 restantes, buscar grupos N:M
   - Resultado: ~30 matches (confianza 0.60-0.80)
5. **Nivel 4:** Clificar los ~12 no conciliados
   - Calcular cuadre: diferencia = 0.00

### Resultado

```json
{
  "estado": "completada",
  "cuadre_diferencia": 0,
  "resumen": {
    "total_movimientos": 3984,
    "conciliados": 1980,
    "no_conciliados": 12,
    "porcentaje_conciliacion": 99.70
  }
}
```

---

## Preguntas Frecuentes

### ¿Por qué no se usa `codigo_movimiento` para matching?

Porque los extractos bancarios **no tienen códigos de movimiento**. El `codigo_movimiento` es un identificador del sistema contable interno ( SAP, Siigo, etc.) que no aparece en los extractos del banco. Por lo tanto, no puede servir como llave de matching.

### ¿Por qué la diferencia puede ser > 0 aunque todos los movimientos estén conciliados?

Porque la fórmula de cuadre compara **saldos** contra **partidas**:

```
Saldo Libros + Partidas Extracto = Saldo Extracto + Partidas Libros
```

Si los saldos iniciales/finales no están alineados con los movimientos del período (por ejemplo, si hay movimientos de períodos anteriores que afectan el saldo), la fórmula no cuadra aunque cada movimiento individual esté matcheado.

### ¿Qué pasa si el PDF parser no puede extraer el saldo final?

El pipeline tiene un **fallback**: si `saldo_final == 0` y hay movimientos, reconstruye los saldos a partir de `saldo_libros` y los totales de movimientos. Esto arregla casos como los parsers de Agrario y Bancolombia que no extraen saldos correctamente.

### ¿Por qué se usa subset-sum en el Nivel 3?

Porque en la vida real, los montos no siempre son 1:1. Un depósito de $500K puede ser la suma de 3 cheques de $200K + $150K + $150K. El algoritmo de subset-sum con backtracking encuentra estas combinaciones de forma eficiente.

### ¿Qué significan los distintos niveles de confianza?

| Nivel | Confianza | Significado |
|-------|-----------|-------------|
| Nivel 1 | 0.85 - 0.95 | Match exacto — alta certeza |
| Nivel 2 | 0.65 - 0.85 | Fecha flexible — buena certeza pero requiere revisión |
| Nivel 3 | 0.60 - 0.80 | Match grupal — certeza moderada, requiere verificación manual |

La confianza se usa para priorizar revisiones humanas, no para descartar matches.

### ¿Qué información proporciona el campo `nota` en cada movimiento?

Cada movimiento en la respuesta incluye un campo `nota` que explica el diagnóstico de la conciliación:

**Movimientos conciliados:**
- Nivel y tipo de match (`nivel 1 (exacto)`, `nivel 2 (fecha_flexible)`)
- ID y descripción del movimiento del extracto (`EXT-0007 (PAGO A TERCEROS AVAL)`)
- Información adicional relevante (`2 dias de diferencia`, `multiples candidatos`)

**Movimientos no conciliados:**
- Candidato encontrado pero fuera de ventana (`candidato EXT-0016 encontrado pero 15 dias fuera de ventana`)
- Movimientos duplicados en la contabilidad (`3 movimientos contables por mismo monto y fecha`)
- Sin contraparte en absoluto (`sin contraparte en el extracto`)
- **Reversión contable:** el movimiento es una reversión y se excluye del matching (`Reversión de CTB-0001 (comprobante NCO-001) - excluido del matching` / `Anulado por CTB-0002 (comprobante NCO-002) - excluido del matching`)

Ejemplos completos:
```
"Conciliado con EXT-0007 (PAGO A TERCEROS AVAL) - nivel 1 (exacto)"
"Conciliado con EXT-0010 (PAGO TERCERO) - nivel 2 (fecha_flexible) - 2 dias de diferencia"
"No conciliado: candidato EXT-0016 (CENIT 3.5B) encontrado pero 15 dias fuera de ventana"
"No conciliado: 3 movimientos contables por mismo monto ($118,886,961.00) y fecha"
"No conciliado: sin contraparte en el extracto"
"Reversión de CTB-0001 (comprobante NCO-001) - excluido del matching"
"Anulado por CTB-0008 (comprobante NCO-605) - excluido del matching"
```

### ¿Qué advertencias de proceso genera el motor?

Además de las advertencias de saldo, el motor genera advertencias a nivel de proceso:

| Tipo | Cuando se genera |
|------|-----------------|
| `movimientos_insuficientes` | Contabilidad tiene menos movimientos que el extracto |
| `movimientos_duplicados` | Mismo monto + misma fecha en múltiples movimientos contables (excluye pares de reversión con `cons_cp_contable`) |
| `intereses_no_contabilizados` | El extracto tiene INTERESES LIQUIDADOS sin contraparte en contabilidad |
