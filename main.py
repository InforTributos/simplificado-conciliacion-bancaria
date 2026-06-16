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

        debito = float(item.get("debito", 0))
        credito = float(item.get("credito", 0))

        if debito > 0:
            valor = debito
            naturaleza = "debito"
        elif credito > 0:
            valor = credito
            naturaleza = "credito"
        else:
            continue  # skip zero-value rows

        referencia = str(item.get("codigo_movimiento", ""))

        movs.append(
            MovimientoContable(
                id=f"CTB-{i:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=referencia,
                referencia=referencia,
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

    # Build matched IDs set from matching results
    matched_ids: set[str] = set()
    for m in match_result.matches:
        if isinstance(m.movimiento_contabilidad, list):
            for mc in m.movimiento_contabilidad:
                matched_ids.add(mc.id)
        else:
            matched_ids.add(m.movimiento_contabilidad.id)

    # Update conciliado flag on original movements
    ctb_index = 0
    movs_response = []
    for item in movs_raw:
        copy = dict(item)
        debito = float(copy.get("debito", 0))
        credito = float(copy.get("credito", 0))
        if debito == 0 and credito == 0:
            copy["conciliado"] = False
            movs_response.append(copy)
            continue
        ctb_index += 1
        ctb_id = f"CTB-{ctb_index:04d}"
        copy["conciliado"] = ctb_id in matched_ids
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
