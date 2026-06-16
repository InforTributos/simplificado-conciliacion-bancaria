"""Sub-agente de extraccion LLM: usa prompts especificos por banco para extraer movimientos.

Recibe BankAnalysis del orquestador + texto del extracto y ejecuta el LLM cascade
con un prompt adaptado al banco/formato detectado.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from concilia_engine.config import LLMConfig
from concilia_engine.models import InfoExtracto, MovimientoExtracto, TokenUsage
from concilia_engine.normalizer import parse_amount, parse_date
from concilia_engine.parsers.llm_orchestrator import BankAnalysis
from concilia_engine.parsers.llm_provider import LiteLLMProvider, _model_limits
from concilia_engine.utils.llm_helpers import clean_and_parse_llm_json

logger = logging.getLogger(__name__)


# Ruta al directorio de prompts relativa a este archivo
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt_template(bank_key: str) -> str | None:
    """Carga el template de prompt para un banco especifico desde YAML.

    Busca en prompts/{bank_key}.yaml.  Retorna el contenido del campo 'prompt'
    o None si no se encuentra.
    """
    prompt_file = _PROMPTS_DIR / f"{bank_key}.yaml"
    if not prompt_file.exists():
        return None
    try:
        import yaml
        with open(prompt_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "prompt" in data:
            return data["prompt"]
    except Exception:
        pass
    return None


def _format_prompt(template: str, analysis: BankAnalysis) -> str:
    """Formatea un template de prompt con los datos del BankAnalysis."""
    columnas_str = ", ".join(analysis.columnas) if analysis.columnas else "desconocidas"
    debito_kw = ", ".join(analysis.claves_debito[:6]) if analysis.claves_debito else "DEBITO, CARGO, RETIRO"
    credito_kw = ", ".join(analysis.claves_credito[:6]) if analysis.claves_credito else "CREDITO, ABONO, CONSIGNACION"

    miles = "coma" if analysis.separador_miles == "comma" else "punto"
    decimal = "punto" if analysis.separador_decimal == "dot" else "coma"

    return template.format(
        banco=analysis.banco or "Desconocido",
        tipo_cuenta=analysis.tipo_cuenta or "desconocida",
        formato_fecha=analysis.formato_fecha or "DD/MM/YYYY",
        columnas=columnas_str,
        separador_miles=miles,
        separador_decimal=decimal,
        prefijo_moneda=analysis.prefijo_moneda or "$",
        debito_kw=debito_kw,
        credito_kw=credito_kw,
    )


def _build_extraction_prompt(texto: str, analysis: BankAnalysis, template: str | None, max_chars: int) -> str:
    """Construye el prompt de extraccion combinando template + BankAnalysis + texto, truncando a max_chars."""
    if template:
        base = _format_prompt(template, analysis)
    else:
        base = _build_generic_prompt(analysis)

    truncado = texto[:max_chars]
    nota = f"\n[... texto truncado a {max_chars} de {len(texto)} caracteres]" if len(texto) > max_chars else ""
    return f"{base}\n\nCONTENIDO DEL EXTRACTO:{nota}\n{truncado}"


def _build_generic_prompt(analysis: BankAnalysis) -> str:
    """Prompt generico cuando no hay template especifico para el banco."""
    columnas_str = ", ".join(analysis.columnas) if analysis.columnas else "detecta las columnas automaticamente"
    miles = "coma" if analysis.separador_miles == "comma" else "punto"
    decimal = "punto" if analysis.separador_decimal == "dot" else "coma"

    return f"""Eres un experto en extractos bancarios colombianos. Analiza el siguiente texto 
(extraido de un PDF bancario) y extrae TODOS los movimientos en formato JSON.

Informacion detectada del extracto:
- Banco: {analysis.banco or 'Desconocido'}
- Tipo de cuenta: {analysis.tipo_cuenta or 'desconocida'}
- Formato de fecha: {analysis.formato_fecha or 'detecta automaticamente'}
- Columnas: {columnas_str}
- Los montos usan {miles} como separador de miles y {decimal} como separador decimal.

Para cada movimiento extrae:
- fecha: en formato YYYY-MM-DD
- descripcion: texto descriptivo del movimiento (sin el monto)
- valor: monto numerico (siempre positivo)
- naturaleza: "debito" si es un cargo/salida/retiro, "credito" si es un abono/entrada/consignacion

Tambien extrae la informacion del extracto:
- banco: nombre del banco
- numero_cuenta: numero de cuenta
- periodo_inicio: fecha inicio del periodo (YYYY-MM-DD)
- periodo_fin: fecha fin del periodo (YYYY-MM-DD)
- saldo_anterior: saldo al inicio del periodo (numerico)
- saldo_final: saldo al final del periodo (numerico)

Responde UNICAMENTE con JSON valido en esta estructura exacta:
{{
  "info": {{
    "banco": "string",
    "numero_cuenta": "string",
    "periodo_inicio": "YYYY-MM-DD",
    "periodo_fin": "YYYY-MM-DD",
    "saldo_anterior": 0.00,
    "saldo_final": 0.00
  }},
  "movimientos": [
    {{
      "fecha": "YYYY-MM-DD",
      "descripcion": "string",
      "valor": 0.00,
      "naturaleza": "debito|credito"
    }}
  ]
}}

IMPORTANTE: Extrae TODOS los movimientos, no omitas ninguno.
NO incluyas markdown ni texto adicional, SOLO el JSON."""


def extract_with_subagent(
    texto: str,
    analysis: BankAnalysis,
    config: LLMConfig,
    bank_key: str = "",
) -> dict | None:
    """Ejecuta el sub-agente de extraccion con el prompt especifico del banco.

    Args:
        texto: Texto completo del extracto (pdfplumber + MarkItDown combinados)
        analysis: BankAnalysis del orquestador
        config: Configuracion LLM (modelos, API keys, cascade)
        bank_key: Clave del banco para cargar template (ej: "bancolombia", "serfinanza")

    Returns:
        dict con 'info' y 'movimientos' parseados, o None si todos los proveedores fallan.
    """
    # Cargar template especifico o usar generico
    template = _load_prompt_template(bank_key) if bank_key else None
    if not template:
        # Intentar con el nombre del banco normalizado
        clean_key = analysis.banco.lower().replace(" ", "_").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        template = _load_prompt_template(clean_key)

    provider = LiteLLMProvider()

    # Cascade: primary, backup, second_backup
    providers = [
        (config.api_key, config.model, "primary"),
        (config.backup_api_key, config.backup_model, "backup"),
        (config.second_backup_api_key, config.second_backup_model, "second_backup"),
    ]

    for api_key, model, label in providers:
        if not api_key or not model:
            logger.debug("Sub-agente: %s no configurado, saltando", label)
            continue

        # Truncar texto segun los limites del modelo especifico
        _, max_chars = _model_limits(model, config.max_tokens, config.max_context_chars)
        prompt = _build_extraction_prompt(texto, analysis, template, max_chars)

        logger.info("Sub-agente: intentando %s (%s, max_chars=%d)...", label, model, max_chars)
        response = provider.generate(prompt, model, api_key, config)
        if response is None:
            logger.warning("Sub-agente: %s no respondio", label)
            continue

        result = _parse_json_response(response)
        if result is not None:
            logger.info("Sub-agente: %s extrajo %d movimientos", label, len(result.get("movimientos", [])))
            return result

    logger.warning("Sub-agente: ningun proveedor pudo extraer movimientos")
    return None


def _parse_json_response(response: str) -> dict | None:
    """Parsea la respuesta JSON del LLM, limpiando markdown y validando."""
    data = clean_and_parse_llm_json(response)

    if data is None:
        return None

    # Validar y limpiar movimientos
    valid_movs = []
    for m in data.get("movimientos", []):
        try:
            fecha_str = m.get("fecha", "")
            fecha = parse_date(fecha_str) if fecha_str else None
            valor = parse_amount(str(m.get("valor", 0)))
            if fecha is None or valor == 0:
                continue
            valid_movs.append({
                "fecha": fecha.isoformat(),
                "descripcion": str(m.get("descripcion", "")).strip(),
                "valor": abs(valor),
                "naturaleza": m.get("naturaleza", "debito"),
            })
        except Exception:
            continue

    data["movimientos"] = valid_movs

    # Validar info
    info = data.get("info", {})
    data["info"] = {
        "banco": str(info.get("banco", "")),
        "numero_cuenta": str(info.get("numero_cuenta", "")),
        "periodo_inicio": str(info.get("periodo_inicio", "")),
        "periodo_fin": str(info.get("periodo_fin", "")),
        "saldo_anterior": float(info.get("saldo_anterior", 0) or 0),
        "saldo_final": float(info.get("saldo_final", 0) or 0),
    }

    return data
