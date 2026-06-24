"""Standalone /procesar API — no DB, no auth, no Celery.

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import json
import re
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

from concilia_engine.config import LLMConfig, MatchConfig
from concilia_engine.models import MovimientoContable
from concilia_engine.pipeline import ejecutar_pipeline_conciliacion
from concilia_engine.validacion import validar_cuenta_contra_pdf, validar_periodo_contra_pdf


# ---------------------------------------------------------------------------
# Settings — only the fields needed by /procesar
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    MAX_FILE_SIZE_MB: int = 50

    LLM_API_KEY: str = ""
    LLM_MODEL: str = "nvidia_nim/nvidia/llama-3.1-nemotron-nano-vl-8b-v1"
    LLM_ORCHESTRATOR_MODEL: str = "nvidia_nim/meta/llama-3.1-8b-instruct"
    LLM_BACKUP_KEY: str = ""
    LLM_BACKUP_MODEL: str = "nvidia_nim/meta/llama-3.1-8b-instruct"
    LLM_SECOND_BACKUP_KEY: str = ""
    LLM_SECOND_BACKUP_MODEL: str = ""
    LLM_VISION_MODEL: str = "nvidia_nim/nvidia/llama-3.1-nemotron-nano-vl-8b-v1"
    LLM_MAX_TOKENS: int = 4096
    LLM_TIMEOUT_SECONDS: int = 45
    LLM_MAX_CONTEXT_CHARS: int = 80000
    GEMINI_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""
    HF_API_KEY: str = ""


settings = Settings()


# ---------------------------------------------------------------------------
# Response schema — only the one used by /procesar
# ---------------------------------------------------------------------------

class ProcesarConciliacionResponse(BaseModel):
    estado: Literal["completada", "no_completada", "error"] = Field(description="Estado del proceso de conciliacion")
    periodo: str | None = Field(default=None, description="Periodo detectado en formato AAAAMM (ej: 202401)")
    movimientos_detalle: list[dict] = Field(default_factory=list, description="Array de movimientos contables con bandera conciliado (true/false)")
    resumen: dict = Field(default_factory=dict, description="Resumen: total_movimientos, conciliados, no_conciliados, porcentaje_conciliacion")
    cuadre_diferencia: float | None = Field(default=None, description="Diferencia de cuadre (0 = cuadra perfectamente)")
    metricas: dict = Field(default_factory=dict, description="Metricas: tiempo_total_ms")
    advertencias: list[dict] = Field(default_factory=list, description="Advertencias no bloqueantes (ej: diferencia de saldos)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = settings.MAX_FILE_SIZE_MB * 1024 * 1024


def _parse_movimientos_detalle(items: list[dict]) -> list[MovimientoContable]:
    """Map the JSON ``movimientos_detalle`` array to engine domain objects."""
    movs = []
    for i, item in enumerate(items, 1):
        fecha_str = item.get("fecha", "")
        try:
            fecha = datetime.strptime(fecha_str, "%d-%m-%Y").date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "estado": "error",
                    "error": {
                        "codigo": "VALIDACION_ERROR",
                        "mensaje": f"Formato de fecha invalido en movimiento {i}: {fecha_str}. Use dd-mm-aaaa",
                        "detalles": [{"campo": "fecha", "motivo": "Formato invalido"}],
                    },
                },
            )

        debito = abs(float(item.get("debito", 0)))
        credito = abs(float(item.get("credito", 0)))

        if debito > 0:
            valor = debito
            naturaleza = "debito"
        elif credito > 0:
            valor = credito
            naturaleza = "credito"
        else:
            continue  # skip zero-value rows

        referencia = str(item.get("codigo_movimiento", ""))
        codig_cp = item.get("codig_cp_contable") or None
        cons_cp = item.get("cons_cp_contable") or None

        movs.append(
            MovimientoContable(
                id=f"CTB-{i:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=referencia,
                referencia=referencia,
                codigo_comprobante=codig_cp,
                cons_cp_contable=cons_cp,
            )
        )

    return movs


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Concilia /procesar API",
    description="Endpoint standalone de conciliacion bancaria — procesa PDF + JSON, sin DB, sin auth.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# POST /api/v1/conciliaciones/procesar
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/conciliaciones/procesar",
    response_model=ProcesarConciliacionResponse,
    summary="Procesar conciliacion bancaria (publico)",
    description="Procesa un extracto bancario (PDF) y los movimientos contables (JSON). "
    "Retorna el resultado completo de la conciliacion sin persistir en base de datos. "
    "No requiere autenticacion.",
    responses={
        400: {"description": "Extracto excede MAX_FILE_SIZE_MB"},
        422: {"description": "Error de validacion: JSON invalido, movimientos vacios, fecha incorrecta, periodo/cuenta no coinciden"},
        500: {"description": "Error interno del servidor"},
    },
)
async def procesar_conciliacion(
    extracto: UploadFile = File(..., description="Extracto bancario en formato PDF"),
    periodo: str | None = Form(default=None, description="Periodo esperado en formato AAAAMM (ej: 202401). Si se omite, se auto-detecta del PDF."),
    cuenta_bancaria: str = Form(default="{}", description="JSON opcional con metadatos de la cuenta: numero_cuenta_bancaria, saldo_anterior_periodo, saldo_actual_periodo"),
    movimientos_detalle: str = Form(..., description="Array JSON de movimientos contables. Campos por objeto: fecha (YYYY-MM-DD), debito, credito, codigo_movimiento"),
):
    """Public reconciliation endpoint — no auth, no persistence."""

    # Validate periodo (only if provided)
    if periodo is not None and not re.match(r"^\d{6}$", periodo):
        raise HTTPException(
            status_code=422,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "VALIDACION_ERROR",
                    "mensaje": "El periodo debe tener formato AAAAMM (ej: 202603)",
                    "detalles": [{"campo": "periodo", "motivo": "Formato invalido"}],
                },
            },
        )

    # Read extracto
    extracto_bytes = await extracto.read()
    if len(extracto_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "ARCHIVO_MUY_GRANDE",
                    "mensaje": f"Extracto excede {settings.MAX_FILE_SIZE_MB}MB",
                    "detalles": [],
                },
            },
        )

    # Parse formulario JSON fields
    try:
        movs_raw: list[dict] = json.loads(movimientos_detalle)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "VALIDACION_ERROR",
                    "mensaje": "movimientos_detalle no es un JSON valido",
                    "detalles": [{"campo": "movimientos_detalle", "motivo": "JSON invalido"}],
                },
            },
        )

    if not movs_raw or not isinstance(movs_raw, list):
        raise HTTPException(
            status_code=422,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "VALIDACION_ERROR",
                    "mensaje": "movimientos_detalle no puede venir vacio",
                    "detalles": [{"campo": "movimientos_detalle", "motivo": "Lista vacia"}],
                },
            },
        )

    # Map to engine domain objects
    try:
        movimientos_ctb = _parse_movimientos_detalle(movs_raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "VALIDACION_ERROR",
                    "mensaje": f"Error al procesar movimientos_detalle: {e}",
                    "detalles": [{"campo": "movimientos_detalle", "motivo": str(e)}],
                },
            },
        )

    # Extract saldo_libros from last movement's saldo (closing balance)
    saldo_libros = None
    if movs_raw:
        try:
            saldo_libros = float(movs_raw[-1].get("saldo", 0))
        except (TypeError, ValueError):
            saldo_libros = None

    # Build default configs (no DB, no empresa-level overrides)
    llm_config = LLMConfig(
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        orchestrator_api_key=settings.LLM_API_KEY,
        orchestrator_model=settings.LLM_ORCHESTRATOR_MODEL,
        backup_api_key=settings.LLM_BACKUP_KEY,
        backup_model=settings.LLM_BACKUP_MODEL,
        second_backup_api_key=settings.LLM_SECOND_BACKUP_KEY,
        second_backup_model=settings.LLM_SECOND_BACKUP_MODEL,
        vision_api_key=settings.LLM_API_KEY,
        vision_model=settings.LLM_VISION_MODEL,
        max_tokens=settings.LLM_MAX_TOKENS,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_context_chars=settings.LLM_MAX_CONTEXT_CHARS,
    )
    config = MatchConfig()

    # Run pipeline
    try:
        pipeline_result = ejecutar_pipeline_conciliacion(
            extracto_bytes=extracto_bytes,
            extracto_filename=extracto.filename or "extracto.pdf",
            movimientos_contables=movimientos_ctb,
            periodo=periodo,
            config=config,
            llm_config=llm_config,
            saldo_libros=saldo_libros,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "PROCESAMIENTO_ERROR",
                    "mensaje": str(e),
                    "detalles": [],
                },
            },
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "ERROR_INTERNO",
                    "mensaje": "Error interno al procesar la conciliacion",
                    "detalles": [],
                },
            },
        )

    # Validate user-provided data against PDF extraction
    parse_info = pipeline_result["parse_result"].info_extracto
    periodo_extraido_aaaamm = f"{parse_info.periodo_inicio.year}{parse_info.periodo_inicio.month:02d}"

    periodo_err = validar_periodo_contra_pdf(periodo, parse_info)
    if periodo_err:
        raise HTTPException(
            status_code=422,
            detail={
                "estado": "error",
                "error": {
                    "codigo": "VALIDACION_PERIODO",
                    "mensaje": "El periodo enviado no concuerda con el extracto",
                    "periodo_recibido": periodo,
                    "periodo_extraido": periodo_extraido_aaaamm,
                },
            },
        )

    saldo_anterior_recibido = None
    saldo_actual_recibido = None

    if cuenta_bancaria != "{}":
        try:
            cb_data = json.loads(cuenta_bancaria)
            cuenta_env = cb_data.get("numero_cuenta_bancaria", "")
            saldo_anterior_recibido = cb_data.get("saldo_anterior_periodo")
            saldo_actual_recibido = cb_data.get("saldo_actual_periodo")
        except (json.JSONDecodeError, TypeError):
            cuenta_env = ""

        cuenta_err = validar_cuenta_contra_pdf(cuenta_env, parse_info)
        if cuenta_err:
            raise HTTPException(
                status_code=422,
                detail={
                    "estado": "error",
                    "error": {
                        "codigo": "VALIDACION_CUENTA",
                        "mensaje": "La cuenta enviada no concuerda con el extracto",
                        "cuenta_recibida": cuenta_env,
                        "cuenta_extraida": parse_info.numero_cuenta,
                    },
                },
            )

    # Format response
    match_result = pipeline_result["match_result"]
    resumen_engine = match_result.resumen

    total_movs = (
        resumen_engine.movimientos_extracto + resumen_engine.movimientos_contabilidad
    )
    unmatched = (
        resumen_engine.no_conciliados_extracto
        + resumen_engine.no_conciliados_contabilidad
    )

    diferencia = match_result.cuadre_final.diferencia if match_result.cuadre_final else 0.0
    estado = "completada" if diferencia == 0 else "no_completada"

    # Build ctb_match_map and matched IDs from matching results
    ctb_match_map = {}
    matched_ids: set[str] = set()
    for m in match_result.matches:
        ctb_sides = m.movimiento_contabilidad if isinstance(m.movimiento_contabilidad, list) else [m.movimiento_contabilidad]
        for mc in ctb_sides:
            matched_ids.add(mc.id)
            ctb_match_map[mc.id] = m

    # Build unmatched extracto list for nota diagnostics
    ext_no_conciliados = match_result.no_conciliados_extracto

    # Build reversal pair map from parsed movements
    cp_to_ctb = {}
    for m in movimientos_ctb:
        if m.codigo_comprobante:
            cp_to_ctb[m.codigo_comprobante] = m.id
    ctb_by_id = {m.id: m for m in movimientos_ctb}
    reversal_exclude_ids: set[str] = set()
    reversal_ctb_map: dict[str, str] = {}  # {original_ctb_id: reversal_ctb_id}
    for m in movimientos_ctb:
        if m.cons_cp_contable and m.cons_cp_contable in cp_to_ctb:
            original_id = cp_to_ctb[m.cons_cp_contable]
            reversal_exclude_ids.add(m.id)
            reversal_exclude_ids.add(original_id)
            reversal_ctb_map[original_id] = m.id

    # Detect duplicated contabilidad movements (same amount + same date)
    dup_counter = {}
    for m in movimientos_ctb:
        if m.id in reversal_exclude_ids:
            continue  # Skip reversal pairs — intentional accounting cancelation
        key = (m.fecha, round(m.valor, 2))
        dup_counter[key] = dup_counter.get(key, 0) + 1
    dup_keys = {k for k, v in dup_counter.items() if v > 1}

    # Update conciliado flag and generate nota on original movements
    ctb_index = 0
    movs_response = []
    for item in movs_raw:
        copy = dict(item)
        debito = abs(float(copy.get("debito", 0)))
        credito = abs(float(copy.get("credito", 0)))
        if debito == 0 and credito == 0:
            copy["conciliado"] = False
            copy["nota"] = ""
            movs_response.append(copy)
            continue
        ctb_index += 1
        ctb_id = f"CTB-{ctb_index:04d}"

        # Reversal pair check (overrides matching result — reversals don't appear in bank)
        if ctb_id in reversal_exclude_ids:
            copy["conciliado"] = False
            ctb_obj = ctb_by_id.get(ctb_id)
            if ctb_obj and ctb_obj.cons_cp_contable:
                original_cp = ctb_obj.cons_cp_contable
                original_id = cp_to_ctb.get(original_cp, "?")
                copy["nota"] = f"Reversión de {original_id} (comprobante {original_cp}) - excluido del matching"
            else:
                rev_id = reversal_ctb_map.get(ctb_id, "?")
                rev_obj = ctb_by_id.get(rev_id)
                rev_cp = rev_obj.codigo_comprobante or "?" if rev_obj else "?"
                copy["nota"] = f"Anulado por {rev_id} (comprobante {rev_cp}) - excluido del matching"
            movs_response.append(copy)
            continue

        conciliado = ctb_id in matched_ids
        copy["conciliado"] = conciliado
        valor = debito if debito > 0 else credito

        if conciliado and ctb_id in ctb_match_map:
            match = ctb_match_map[ctb_id]
            ext_side = match.movimiento_extracto
            ext_obj = ext_side[0] if isinstance(ext_side, list) else ext_side
            parts = [f"Conciliado con {ext_obj.id} ({ext_obj.descripcion[:40]})"]
            parts.append(f"nivel {match.nivel} ({match.tipo})")
            if match.dias_diferencia:
                parts.append(f"{match.dias_diferencia} dias de diferencia")
            if match.multiple_candidates:
                parts.append("multiples candidatos")
            copy["nota"] = " - ".join(parts)
        else:
            nota = ""
            fecha_str = item.get("fecha", "")
            try:
                from datetime import datetime as _dt
                ctb_fecha = _dt.strptime(fecha_str, "%d-%m-%Y").date()
            except ValueError:
                ctb_fecha = None

            candidate = None
            best_dias = float("inf")
            for ext in ext_no_conciliados:
                if abs(ext.valor - valor) < 0.01:
                    if ctb_fecha:
                        dias = abs((ext.fecha - ctb_fecha).days)
                        if dias < best_dias:
                            best_dias = dias
                            candidate = ext
                    else:
                        candidate = ext
                        break

            if candidate is not None and ctb_fecha is not None and best_dias > config.max_dias_diferencia:
                nota = f"No conciliado: candidato {candidate.id} ({candidate.descripcion[:30]}) encontrado pero {best_dias} dias fuera de ventana"
            elif candidate is not None:
                nota = f"No conciliado: candidato {candidate.id} ({candidate.descripcion[:30]}) encontrado pero naturaleza no coincide tras inversion"
            elif ctb_fecha and (ctb_fecha, round(valor, 2)) in dup_keys:
                dup_count = dup_counter.get((ctb_fecha, round(valor, 2)), 1)
                nota = f"No conciliado: {dup_count} movimientos contables por mismo monto ({valor:,.2f}) y fecha"
            else:
                nota = "No conciliado: sin contraparte en el extracto"

            copy["nota"] = nota

        movs_response.append(copy)

    # Build advertencias from saldo comparisons (never block)
    advertencias = []
    if saldo_anterior_recibido is not None:
        try:
            saldo_ant = float(saldo_anterior_recibido)
            if abs(saldo_ant - parse_info.saldo_anterior) > 0.01:
                advertencias.append({
                    "tipo": "saldo_anterior",
                    "mensaje": "Saldo anterior no coincide",
                    "valor_recibido": saldo_ant,
                    "valor_extraido": parse_info.saldo_anterior,
                })
        except (TypeError, ValueError):
            pass

    if saldo_actual_recibido is not None:
        try:
            saldo_act = float(saldo_actual_recibido)
            if abs(saldo_act - parse_info.saldo_final) > 0.01:
                advertencias.append({
                    "tipo": "saldo_actual",
                    "mensaje": "Saldo actual no coincide",
                    "valor_recibido": saldo_act,
                    "valor_extraido": parse_info.saldo_final,
                })
        except (TypeError, ValueError):
            pass

    if diferencia > 0:
        advertencias.append({
            "tipo": "cuadre_diferencia",
            "mensaje": f"La conciliacion tiene una diferencia de {diferencia:,.2f}",
            "diferencia": diferencia,
        })

    # Process-level warnings
    ctb_count = len(movimientos_ctb)
    ext_count = resumen_engine.movimientos_extracto

    if ctb_count < ext_count:
        advertencias.append({
            "tipo": "movimientos_insuficientes",
            "mensaje": f"Se enviaron {ctb_count} movimientos contables pero el extracto tiene {ext_count}. Puede haber movimientos del banco sin registrar en contabilidad.",
            "movimientos_contables": ctb_count,
            "movimientos_extracto": ext_count,
        })

    if dup_keys:
        total_dup = sum(v for k, v in dup_counter.items() if k in dup_keys)
        advertencias.append({
            "tipo": "movimientos_duplicados",
            "mensaje": f"Se detectaron {total_dup} movimientos contables duplicados (mismo monto y fecha) en {len(dup_keys)} grupos.",
            "grupos_duplicados": len(dup_keys),
            "movimientos_afectados": total_dup,
        })

    intereses_count = sum(1 for ext in ext_no_conciliados if "INTERESES LIQUIDADOS" in ext.descripcion.upper())
    if intereses_count > 0:
        advertencias.append({
            "tipo": "intereses_no_contabilizados",
            "mensaje": f"El extracto tiene {intereses_count} movimientos de intereses no registrados en contabilidad.",
            "intereses_sin_conciliar": intereses_count,
        })

    return ProcesarConciliacionResponse(
        estado=estado,
        periodo=pipeline_result["periodo"],
        movimientos_detalle=movs_response,
        resumen={
            "total_movimientos": total_movs,
            "conciliados": total_movs - unmatched,
            "no_conciliados": unmatched,
            "porcentaje_conciliacion": resumen_engine.porcentaje_conciliacion,
        },
        cuadre_diferencia=diferencia,
        metricas={"tiempo_total_ms": pipeline_result["elapsed_ms"]},
        advertencias=advertencias,
    )
