"""Vision-based parser for scanned/image PDFs using a VL model.

When pdfplumber / pypdf cannot extract text (0 chars), this parser uses
PyMuPDF to render PDF pages as images and sends them to a vision-language
model for OCR + bank-statement extraction.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from concilia_engine.config import LLMConfig
from concilia_engine.models import InfoExtracto, MovimientoExtracto, TokenUsage
from concilia_engine.normalizer import normalize_description, parse_amount, parse_date
from concilia_engine.utils.llm_helpers import clean_and_parse_llm_json

logger = logging.getLogger(__name__)

# Max pages to render for VL extraction (cost control)
MAX_VISION_PAGES = 3
# Max image dimension sent to VL model (bytes / megapixel limit)
MAX_IMAGE_DIM = 1200


_EXTRACTION_PROMPT = """Eres un experto en extractos bancarios colombianos.
Analiza esta imagen de un extracto bancario y extrae TODOS los movimientos.

Para cada movimiento extrae:
- fecha: en formato YYYY-MM-DD
- descripcion: texto descriptivo del movimiento (sin el monto)
- valor: monto numerico (siempre positivo)
- naturaleza: "debito" si es un cargo/salida, "credito" si es un abono/entrada

Tambien extrae la informacion del extracto:
- banco: nombre del banco
- numero_cuenta: numero de cuenta
- periodo_inicio: fecha inicio del periodo (YYYY-MM-DD)
- periodo_fin: fecha fin del periodo (YYYY-MM-DD)
- saldo_anterior: saldo al inicio del periodo (numerico)
- saldo_final: saldo al final del periodo (numerico)

Responde UNICAMENTE con JSON valido:
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
    {"fecha": "YYYY-MM-DD", "descripcion": "string", "valor": 0.00, "naturaleza": "debito|credito"}
  ]
}
NO incluyas markdown ni texto adicional, SOLO el JSON."""


@dataclass
class VisionParseResult:
    movimientos: list = field(default_factory=list)
    info_extracto: InfoExtracto | None = None
    parser_utilizado: str = "vision_parser"
    token_usage: Optional[TokenUsage] = None


class VisionParser:
    """Parse scanned/image PDFs using a VL model."""

    def puede_parsear(self, file_bytes: bytes) -> bool:
        """Check if this is a PDF with renderable pages."""
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            return doc.page_count > 0
        except Exception:
            return False

    def parsear(
        self, file_bytes: bytes, llm_config: LLMConfig, filename: str = "document.pdf"
    ) -> VisionParseResult | None:
        """Render pages as images and extract with VL model."""
        import fitz

        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception:
            return None

        if doc.page_count == 0:
            return None

        images_base64 = []
        for i in range(min(doc.page_count, MAX_VISION_PAGES)):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            # Resize if too large
            scale = min(1.0, MAX_IMAGE_DIM / max(pix.width, pix.height))
            if scale < 1.0:
                pix = page.get_pixmap(
                    dpi=int(150 * scale),
                )
            img_bytes = pix.tobytes("png")
            images_base64.append(base64.b64encode(img_bytes).decode("ascii"))
            logger.info(
                "Vision: rendered page %d/%d (%dx%d, %.1f KB)",
                i + 1, doc.page_count, pix.width, pix.height, len(img_bytes) / 1024,
            )

        if not images_base64:
            return None

        response_text = self._call_vision_model(images_base64, llm_config)
        if response_text is None:
            return None

        data = clean_and_parse_llm_json(response_text)
        if data is None:
            return None

        return self._build_result(data, llm_config.vision_model or llm_config.model)

    def _call_vision_model(
        self, images_base64: list[str], llm_config: LLMConfig
    ) -> str | None:
        """Send images to VL model via LiteLLM."""
        from litellm import completion

        model = llm_config.vision_model or llm_config.model
        api_key = llm_config.vision_api_key or llm_config.api_key

        if not api_key:
            logger.warning("Vision: no API key configured")
            return None

        # Build multimodal content: text + images
        content = [{"type": "text", "text": _EXTRACTION_PROMPT}]
        for img_b64 in images_base64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            })

        try:
            response = completion(
                model=model,
                messages=[{"role": "user", "content": content}],
                api_key=api_key,
                max_tokens=2048,
                timeout=min(llm_config.timeout, 120),
            )
            return response.choices[0].message.content or None
        except Exception as e:
            logger.warning("Vision LLM call failed: %s", str(e)[:200])
            return None

    def _build_result(self, data: dict, model: str) -> VisionParseResult:
        movimientos = []
        for i, mov in enumerate(data.get("movimientos", []), 1):
            fecha = parse_date(mov.get("fecha", "")) if mov.get("fecha") else None
            valor_str = str(mov.get("valor", 0))
            valor = parse_amount(valor_str)
            if not fecha or not valor:
                continue
            movimientos.append(MovimientoExtracto(
                id=f"VIS-{i:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=mov.get("naturaleza", "debito"),
                descripcion=normalize_description(str(mov.get("descripcion", ""))),
            ))

        info_data = data.get("info", {})
        info = InfoExtracto(
            banco=str(info_data.get("banco", "Desconocido (Vision)")),
            numero_cuenta=str(info_data.get("numero_cuenta", "")),
            periodo_inicio=parse_date(info_data.get("periodo_inicio", "")) or date.today(),
            periodo_fin=parse_date(info_data.get("periodo_fin", "")) or date.today(),
            saldo_anterior=float(info_data.get("saldo_anterior", 0) or 0),
            saldo_final=float(info_data.get("saldo_final", 0) or 0),
        )

        return VisionParseResult(
            movimientos=movimientos,
            info_extracto=info,
            parser_utilizado="vision_parser",
        )
