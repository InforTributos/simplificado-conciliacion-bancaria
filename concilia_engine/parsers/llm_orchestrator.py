"""Orquestador LLM: analiza texto de extracto bancario para identificar banco, formato y columnas.

Usa deteccion rapida via puede_parsear() cuando el banco es conocido.
Si no hay match, invoca Gemini Flash (modelo mas barato) para analizar la estructura.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from concilia_engine.config import LLMConfig
from concilia_engine.parsers.llm_provider import LiteLLMProvider, _model_limits

logger = logging.getLogger(__name__)


@dataclass
class BankAnalysis:
    """Metadata extraida del analisis del extracto."""
    banco: str = ""
    tipo_cuenta: str = ""
    formato_fecha: str = ""          # "DD/MM/YYYY", "DD/MM", "YYYY/MM/DD", etc.
    separador_miles: str = "comma"   # "comma" o "dot"
    separador_decimal: str = "dot"   # "dot" o "comma"
    columnas: list[str] = field(default_factory=list)
    prefijo_moneda: str = "$"
    tiene_movimientos: bool = True
    claves_debito: list[str] = field(default_factory=lambda: ["DEBITO", "CARGO", "RETIRO", "PAGO", "GMF", "IVA", "COMISION"])
    claves_credito: list[str] = field(default_factory=lambda: ["CREDITO", "ABONO", "CONSIGNACION", "TRANSFERENCIA", "RENDIMIENTO", "INTERES", "NC"])
    observaciones: str = ""


def _build_investigation_prompt(texto: str, max_chars: int = 64000) -> str:
    """Construye el prompt para que el LLM analice la estructura del extracto."""
    truncado = texto[:max_chars]
    nota = f"\n[... texto truncado a {max_chars} de {len(texto)} caracteres]" if len(texto) > max_chars else ""
    return f"""Eres un experto en extractos bancarios colombianos. Analiza el siguiente texto extraido de un PDF bancario e identifica:

1. **banco**: nombre del banco o entidad financiera
2. **tipo_cuenta**: "ahorros", "corriente", "fiduciaria", "inversion", "fondo" o "desconocido"
3. **formato_fecha**: patron de fechas usado ("DD/MM/YYYY", "DD-MM-YYYY", "YYYY/MM/DD", "DD/MM", "DD MMM", etc.)
4. **separador_miles**: "comma" si usa coma (1,000.00) o "dot" si usa punto (1.000.00)
5. **separador_decimal**: "dot" si usa punto (1,000.00) o "comma" si usa coma (1.000,00)
6. **columnas**: lista con los nombres de columna en orden (ej: ["fecha", "descripcion", "oficina", "documento", "valor", "saldo"])
7. **prefijo_moneda**: "$" si los montos tienen signo peso, "" si no
8. **tiene_movimientos**: true si hay lineas de movimientos, false si es solo resumen/rentabilidad
9. **claves_debito**: palabras clave que indican debito/cargo en el extracto
10. **claves_credito**: palabras clave que indican credito/abono en el extracto

Responde UNICAMENTE con JSON valido en esta estructura:
{{
  "banco": "string",
  "tipo_cuenta": "string",
  "formato_fecha": "string",
  "separador_miles": "comma|dot",
  "separador_decimal": "dot|comma",
  "columnas": ["string"],
  "prefijo_moneda": "$",
  "tiene_movimientos": true,
  "claves_debito": ["string"],
  "claves_credito": ["string"]
}}

NO incluyas markdown ni texto adicional, SOLO el JSON.

TEXTO DEL EXTRACTO:{nota}
{truncado}"""


def analyze_extract(texto: str, config: LLMConfig) -> BankAnalysis | None:
    """Analiza el texto del extracto via LLM (orchestrator model, cheap/fast).
    
    Returns BankAnalysis con la metadata del extracto, o None si falla.
    """
    provider = LiteLLMProvider()
    
    # Usar el modelo de orquestador (barato/rapido) o fallback al primario
    model = config.orchestrator_model or config.model
    api_key = config.orchestrator_api_key or config.api_key
    
    _, max_chars = _model_limits(model, config.max_tokens, config.max_context_chars)
    prompt = _build_investigation_prompt(texto, max_chars)
    
    response = provider.generate(prompt, model, api_key, config)
    if response is None:
        logger.warning("Orquestador: sin respuesta del LLM")
        return None
    
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Orquestador: respuesta no es JSON valido")
        return None
    
    return BankAnalysis(
        banco=data.get("banco", ""),
        tipo_cuenta=data.get("tipo_cuenta", ""),
        formato_fecha=data.get("formato_fecha", ""),
        separador_miles=data.get("separador_miles", "comma"),
        separador_decimal=data.get("separador_decimal", "dot"),
        columnas=data.get("columnas", []),
        prefijo_moneda=data.get("prefijo_moneda", "$"),
        tiene_movimientos=data.get("tiene_movimientos", True),
        claves_debito=data.get("claves_debito", ["DEBITO", "CARGO", "RETIRO", "PAGO"]),
        claves_credito=data.get("claves_credito", ["CREDITO", "ABONO", "CONSIGNACION", "INTERES"]),
    )
