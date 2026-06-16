"""LLM fallback parser using LiteLLM with multi-provider cascade and retry.

Provider chain (all free tiers):
  1. PRIMARY  — Gemini 2.0 Flash              (gemini/gemini-2.0-flash)
  2. BACKUP   — NVIDIA NIM Llama 3.1 8B        (nvidia_nim/meta/llama-3.1-8b-instruct)
  3. SECOND   — HuggingFace Phi-3.5-mini       (huggingface/microsoft/Phi-3.5-mini-instruct)

Each provider retries transient / rate-limit errors internally before the
chain falls through to the next provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from concilia_engine.config import LLMConfig
from concilia_engine.models import InfoExtracto, MovimientoExtracto, TokenUsage
from concilia_engine.normalizer import normalize_description, parse_amount, parse_date
from concilia_engine.parsers.llm_provider import _model_limits
from concilia_engine.utils.llm_helpers import clean_and_parse_llm_json

logger = logging.getLogger(__name__)


@dataclass
class LLMParseResult:
    movimientos: list[MovimientoExtracto]
    info_extracto: InfoExtracto
    token_usage: TokenUsage


EXTRACTION_PROMPT = """Eres un experto en extractos bancarios colombianos. Analiza el siguiente texto (extraido de un PDF bancario) y extrae TODOS los movimientos en formato JSON.

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
{
  "info": {
    "banco": "string",
    "numero_cuenta": "string",
    "periodo_inicio": "YYYY-MM-DD",
    "periodo_fin": "YYYY-MM-DD",
    "saldo_anterior": 0.00,
    "saldo_final": 0.00
  },
  "movimientos": [
    {
      "fecha": "YYYY-MM-DD",
      "descripcion": "string",
      "valor": 0.00,
      "naturaleza": "debito|credito"
    }
  ]
}

IMPORTANTE: Extrae TODOS los movimientos, no omitas ninguno. El texto puede contener caracteres especiales por ser extraido de un PDF, interpretalos correctamente.
NO incluyas markdown ni texto adicional, SOLO el JSON.

CONTENIDO DEL EXTRACTO:
"""


class LLMParser:
    """Parse bank statements using LiteLLM with multi-provider fallback.

    Each provider is tried in order.  If one fails after exhausting its
    internal retries, the chain falls through to the next provider.
    """

    def __init__(self):
        from concilia_engine.parsers.llm_provider import LiteLLMProvider

        self._provider = LiteLLMProvider()

    def _parse_one_provider(
        self, api_key: str, model: str, texto: str, config: LLMConfig
    ) -> LLMParseResult | None:
        """Attempt extraction with a single provider.

        Returns ``None`` if the provider fails (rate limit, JSON error, etc.).
        """
        if not api_key:
            return None

        # Truncar texto segun limites del modelo
        _, max_chars = _model_limits(model, config.max_tokens, config.max_context_chars)
        texto_truncado = texto[:max_chars]
        if len(texto) > max_chars:
            texto_truncado += f"\n\n[... truncado, {len(texto) - max_chars} caracteres omitidos]"

        full_prompt = EXTRACTION_PROMPT + texto_truncado

        try:
            response_text = self._provider.generate(full_prompt, model, api_key, config)
        except Exception as e:
            logger.warning("LLM (%s): unexpected error: %s", model, str(e)[:200])
            return None

        if response_text is None:
            return None

        return self._parse_json_response(response_text, model, config)

    def _parse_json_response(
        self, response_text: str, model: str, config: LLMConfig
    ) -> LLMParseResult | None:
        """Parse the JSON response from an LLM provider."""
        data = clean_and_parse_llm_json(response_text)

        if data is None:
            logger.warning("LLM (%s): response was not valid JSON", model)
            return None

        # Build token usage (no token counts from this path — counted per-call
        # in a future enhancement via response.usage)
        token_usage = TokenUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            model_used=model,
            estimated_cost_usd=0.0,
        )

        # Parse movements
        movimientos = []
        for i, mov in enumerate(data.get("movimientos", []), 1):
            fecha = parse_date(mov.get("fecha", ""))
            valor = mov.get("valor")
            if isinstance(valor, str):
                valor = parse_amount(valor)
            if not fecha or not valor:
                continue
            movimientos.append(MovimientoExtracto(
                id=f"EXT-{i:04d}",
                fecha=fecha,
                valor=abs(float(valor)),
                naturaleza=mov.get("naturaleza", "debito"),
                descripcion=normalize_description(mov.get("descripcion", "")),
            ))

        # Parse info
        info_data = data.get("info", {})
        info = InfoExtracto(
            banco=info_data.get("banco", "Desconocido (LLM)"),
            numero_cuenta=info_data.get("numero_cuenta", ""),
            periodo_inicio=parse_date(info_data.get("periodo_inicio", "")) or date.today(),
            periodo_fin=parse_date(info_data.get("periodo_fin", "")) or date.today(),
            saldo_anterior=float(info_data.get("saldo_anterior", 0)),
            saldo_final=float(info_data.get("saldo_final", 0)),
        )

        logger.info(
            "LLM (%s): extracted %d movements",
            model, len(movimientos),
        )

        return LLMParseResult(
            movimientos=movimientos,
            info_extracto=info,
            token_usage=token_usage,
        )

    def parsear_con_llm(self, texto: str, config: LLMConfig) -> LLMParseResult | None:
        """Parse statement text using the LLM cascade chain."""
        providers = [
            ("primary", config.api_key, config.model),
        ]
        if config.backup_model and config.backup_api_key:
            providers.append(("backup", config.backup_api_key, config.backup_model))
        if config.second_backup_model and config.second_backup_api_key:
            providers.append(("second_backup", config.second_backup_api_key, config.second_backup_model))

        for label, api_key, model in providers:
            logger.info("LLM: trying %s provider (%s)", label, model)
            result = self._parse_one_provider(api_key, model, texto, config)
            if result is not None:
                return result

        return None
