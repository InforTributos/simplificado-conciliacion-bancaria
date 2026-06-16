"""Parser router — detects bank and routes to appropriate parser.

Flujo principal:
  1. Text extraction (pdfplumber for regex, MarkItDown for LLM)
  2. Regex parsers (bank-specific, rapido)
  3. Orquestador LLM (analiza formato, detecta banco)
  4. Sub-agente LLM (extraccion con prompt especifico por banco)
  5. GenericParser (fallback final)
"""

from __future__ import annotations

import io
import logging
import time
from datetime import date
from typing import TYPE_CHECKING

import pdfplumber
import pypdf

from concilia_engine.config import LLMConfig, ParseConfig
from concilia_engine.models import InfoExtracto, ParseResult

if TYPE_CHECKING:
    from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)


class ParserRouter:
    """Routes statement files to the appropriate bank parser."""

    def __init__(self, parsers: list[BankParser] | None = None):
        if parsers is None:
            parsers = self._default_parsers()
        self._parsers = parsers
        self._generic_parser = None
        self._llm_parser = None

    def _default_parsers(self) -> list[BankParser]:
        from concilia_engine.parsers.fic import FICParser
        from concilia_engine.parsers.bbva import BBVAParser
        from concilia_engine.parsers.bogota import BogotaParser
        from concilia_engine.parsers.occidente import OccidenteParser
        from concilia_engine.parsers.bancolombia import BancolombiaParser
        from concilia_engine.parsers.davivienda import DaviviendaParser
        from concilia_engine.parsers.davibanck import DavibanckParser
        from concilia_engine.parsers.serfinanza import SerfinanzaParser
        from concilia_engine.parsers.banco_gnb import BancoGNBParser
        from concilia_engine.parsers.banco_popular import BancoPopularParser
        from concilia_engine.parsers.bancoomeva import BancoomevaParser
        from concilia_engine.parsers.avvillas import AvVillasParser
        from concilia_engine.parsers.colpatria import ColpatriaParser
        from concilia_engine.parsers.banco_caja_social import BancoCajaSocialParser
        from concilia_engine.parsers.itau import ItauParser
        from concilia_engine.parsers.banco_agrario import BancoAgrarioParser

        return [
            FICParser(),
            BBVAParser(),
            ItauParser(),
            BogotaParser(),
            OccidenteParser(),
            BancolombiaParser(),
            DaviviendaParser(),
            DavibanckParser(),
            SerfinanzaParser(),
            BancoGNBParser(),
            BancoPopularParser(),
            BancoomevaParser(),
            AvVillasParser(),
            ColpatriaParser(),
            BancoCajaSocialParser(),
            BancoAgrarioParser(),
        ]

    def _get_generic(self):
        if self._generic_parser is None:
            from concilia_engine.parsers.generic import GenericParser
            self._generic_parser = GenericParser()
        return self._generic_parser

    def _get_llm(self):
        if self._llm_parser is None:
            from concilia_engine.parsers.llm import LLMParser
            self._llm_parser = LLMParser()
        return self._llm_parser

    # ------------------------------------------------------------------
    # Text extraction (plain text for regex, markdown for LLM)
    # ------------------------------------------------------------------

    def _extract_text_plain(self, file_bytes: bytes) -> str:
        """Extract plain text from PDF using pdfplumber (preferred) or pypdf."""
        text_parts = []
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
        except Exception:
            logger.warning("pdfplumber failed, trying pypdf")

        if not text_parts:
            try:
                reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            except Exception:
                logger.error("Both PDF parsers failed")

        return "\n".join(text_parts)

    def _extract_markitdown(self, file_bytes: bytes, filename: str) -> str | None:
        """Try to convert PDF to markdown via MarkItDown. Returns None on failure."""
        if file_bytes[:4] != b"%PDF":
            return None
        try:
            from concilia_engine.parsers.markitdown_converter import get_converter
            return get_converter().convert(file_bytes, filename)
        except Exception:
            return None

    def extract_text_from_pdf(
        self, file_bytes: bytes, filename: str = "document.pdf", use_markitdown: bool = False
    ) -> str:
        """Extract text from a PDF file.

        When *use_markitdown* is True, MarkItDown markdown is attempted first.
        Falls back to pdfplumber + pypdf plain-text extraction.
        """
        if use_markitdown:
            md_text = self._extract_markitdown(file_bytes, filename)
            if md_text:
                return md_text

        return self._extract_text_plain(file_bytes)

    def extract_text_from_txt(self, file_bytes: bytes) -> str:
        """Extract text from TXT with encoding detection (UTF-8, Latin-1, CP-1252)."""
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                return file_bytes.decode(encoding)
            except (UnicodeDecodeError, ValueError):
                continue
        return file_bytes.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Bank detection
    # ------------------------------------------------------------------

    def detect_bank(self, texto: str) -> BankParser | None:
        """Detect which bank parser can handle this statement."""
        for parser in self._parsers:
            if parser.puede_parsear(texto):
                logger.info("Bank detected: %s", parser.banco_nombre)
                return parser
        return None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def parse_extracto(
        self,
        file_bytes: bytes,
        filename: str,
        config: ParseConfig | None = None,
        llm_config: LLMConfig | None = None,
    ) -> ParseResult:
        """Main entry: detect bank -> parse -> normalize -> validate.

        Uses **dual text extraction**:
        * Plain text (pdfplumber) → bank-specific regex parsers + generic parser.
        * Markdown text (MarkItDown) → LLM fallback only.
        
        New pipeline when regex fails:
        * Orquestador LLM → analiza formato/banco
        * Sub-agente LLM → extraccion con prompt especifico por banco
        """
        config = config or ParseConfig()
        start_time = time.time()

        # Pre-validate PDF (empty, encrypted, corrupt)
        if filename.lower().endswith(".pdf"):
            error = _validate_pdf(file_bytes, config)
            if error:
                return ParseResult(
                    movimientos=[],
                    info_extracto=InfoExtracto(
                        banco="", numero_cuenta="",
                        periodo_inicio=date.today(), periodo_fin=date.today(),
                        saldo_anterior=0.0, saldo_final=0.0,
                    ),
                    parser_utilizado="validacion",
                    parser_fallback=False,
                    error=error,
                )

        # Extract texts
        if filename.lower().endswith(".pdf"):
            texto_plano = self._extract_text_plain(file_bytes)
            texto_llm = texto_plano
            if config.use_markitdown and llm_config:
                md_text = self._extract_markitdown(file_bytes, filename)
                if md_text:
                    texto_llm = md_text
        else:
            texto_plano = self.extract_text_from_txt(file_bytes)
            texto_llm = texto_plano

        if not texto_plano.strip():
            # Text extraction failed entirely — try MarkItDown, then Vision
            if filename.lower().endswith(".pdf"):
                md_text = self._extract_markitdown(file_bytes, filename)
                if md_text and md_text.strip():
                    logger.info("Plain text extraction failed, using MarkItDown only")
                    if llm_config and llm_config.api_key:
                        return self._parse_with_orchestrator(md_text, llm_config, start_time)
                    generic = self._get_generic()
                    movs = generic.parsear(md_text)
                    info = generic.extraer_info(md_text)
                    return ParseResult(
                        movimientos=movs, info_extracto=info,
                        parser_utilizado="generico (markitdown_only)",
                        parser_fallback=False,
                    )
                # Try VisionParser for scanned/image PDFs
                if llm_config and _is_vision_configured(llm_config):
                    result = self._parse_with_vision(file_bytes, filename, llm_config, start_time)
                    if result is not None:
                        return result
                # Return structured error instead of raising ValueError
                return ParseResult(
                    movimientos=[],
                    info_extracto=InfoExtracto(
                        banco="", numero_cuenta="",
                        periodo_inicio=date.today(), periodo_fin=date.today(),
                        saldo_anterior=0.0, saldo_final=0.0,
                    ),
                    parser_utilizado="validacion",
                    parser_fallback=False,
                    error={
                        "codigo": "ERR_PDF_SOLO_IMAGEN",
                        "mensaje": "El PDF no contiene texto digital. Es probable que sea un escaneo o imagen.",
                        "accion": "reintentar_vision" if (llm_config and _is_vision_configured(llm_config)) else "rechazar",
                    },
                )
            raise ValueError(
                "No se pudo extraer texto del archivo."
            )

        # Force LLM path (uses orchestrator + sub-agent)
        if config.forzar_llm and llm_config:
            return self._parse_with_orchestrator(texto_llm, llm_config, start_time, texto_plano)

        # Try specialized parser (plain text)
        parser = self.detect_bank(texto_plano)
        if parser:
            movimientos = parser.parsear(texto_plano)
            info = parser.extraer_info(texto_plano)
            return ParseResult(
                movimientos=movimientos,
                info_extracto=info,
                parser_utilizado=parser.banco_nombre,
                parser_fallback=False,
                error=_check_math(info, movimientos),
            )

        # Fallback: generic parser (plain text)
        generic = self._get_generic()
        movimientos = generic.parsear(texto_plano)
        info = generic.extraer_info(texto_plano)

        # Check extraction rate — if low, try orchestrator + sub-agent
        total_lines = len([l for l in texto_plano.split("\n") if l.strip()])
        extraction_rate = len(movimientos) / max(total_lines, 1)

        if extraction_rate < 0.50 and llm_config and llm_config.api_key:
            logger.info(
                "Generic parser extraction rate %.2f < 0.50, trying LLM orchestration",
                extraction_rate,
            )
            return self._parse_with_orchestrator(texto_llm, llm_config, start_time, texto_plano)

        return ParseResult(
                    movimientos=movimientos,
                    info_extracto=info,
                    parser_utilizado="generico",
                    parser_fallback=False,
                    error=_detect_no_es_extracto(texto_plano, len(movimientos))
                          or _check_math(info, movimientos),
                )

    # ------------------------------------------------------------------
    # LLM pipeline: orquestador → sub-agente
    # ------------------------------------------------------------------

    def _parse_with_orchestrator(
        self, texto: str, llm_config: LLMConfig, start_time: float,
        texto_plano: str | None = None,
    ) -> ParseResult:
        """New LLM pipeline: orchestrate analysis, then extract with bank-specific prompt.

        Step 1: Orquestador analyzes text to identify bank/format (fast model).
        Step 2: Sub-agente extracts movements with bank-specific prompt (3-level cascade).
        Step 3: If all LLM fails, fall back to generic parser using texto_plano if available.
        """
        from concilia_engine.parsers.llm_orchestrator import analyze_extract
        from concilia_engine.parsers.llm_subagent import extract_with_subagent

        # Step 1: Analyze with orchestrator (cheapest model)
        analysis = None
        try:
            analysis = analyze_extract(texto, llm_config)
        except Exception as e:
            logger.warning("Orquestador LLM fallo: %s", e)

        if analysis and analysis.tiene_movimientos:
            # Step 2: Extract with sub-agent (bank-specific prompt)
            bank_key = _resolve_bank_key(analysis.banco)
            try:
                result = extract_with_subagent(texto, analysis, llm_config, bank_key)
            except Exception as e:
                logger.warning("Sub-agente LLM fallo: %s", e)
                result = None

            if result and result.get("movimientos"):
                movimientos = _build_movimientos(result["movimientos"])
                info = _build_info_extracto(result.get("info", {}))
                return ParseResult(
                    movimientos=movimientos,
                    info_extracto=info,
                    parser_utilizado=f"llm_subagent_{bank_key}",
                    parser_fallback=False,
                )

        # Step 3: Fallback — try legacy LLM parser, then generic
        logger.info("Orquestador/sub-agente fallaron, intentando LLM generico")
        return self._parse_with_llm(texto, llm_config, start_time, texto_plano)

    def _parse_with_llm(
        self, texto: str, llm_config: LLMConfig, start_time: float,
        texto_plano: str | None = None,
    ) -> ParseResult:
        """Parse using LLM fallback with the given (optimized) text."""
        llm = self._get_llm()
        result = llm.parsear_con_llm(texto, llm_config)
        if result is None:
            # Use texto_plano for the generic fallback when available
            # (LLM-optimized text may be truncated or restructured)
            generic_text = texto_plano if texto_plano else texto
            generic = self._get_generic()
            movimientos = generic.parsear(generic_text)
            info = generic.extraer_info(generic_text)
            logger.warning("LLM unavailable, using generic parser results")
            return ParseResult(
                movimientos=movimientos,
                info_extracto=info,
                parser_utilizado="generico (llm_no_disponible)",
                parser_fallback=False,
            )

        return ParseResult(
            movimientos=result.movimientos,
            info_extracto=result.info_extracto,
            parser_utilizado="llm_fallback",
            parser_fallback=True,
            token_usage=result.token_usage,
        )

    def _parse_with_vision(
        self, file_bytes: bytes, filename: str, llm_config: LLMConfig, start_time: float
    ) -> ParseResult | None:
        """Parse using VisionParser for scanned/image PDFs (VL model)."""
        from concilia_engine.parsers.vision_parser import VisionParser

        try:
            vision = VisionParser()
            if not vision.puede_parsear(file_bytes):
                return None
            result = vision.parsear(file_bytes, llm_config, filename)
            if result is None or not result.movimientos:
                return None
            return ParseResult(
                movimientos=result.movimientos,
                info_extracto=result.info_extracto or InfoExtracto(
                    banco="Desconocido (Vision)",
                    numero_cuenta="",
                    periodo_inicio=date.today(),
                    periodo_fin=date.today(),
                    saldo_anterior=0.0,
                    saldo_final=0.0,
                ),
                parser_utilizado="vision_parser",
                parser_fallback=False,
            )
        except Exception as e:
            logger.warning("VisionParser failed: %s", e)
            return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resolve_bank_key(banco: str) -> str:
    """Normalize bank name to a registry key."""
    if not banco:
        return "generic"
    name = banco.lower().strip()
    # Remove accents
    import unicodedata
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = name.replace(" ", "_").replace("-", "_").replace(".", "")
    return name


def _build_movimientos(movs_data: list[dict]) -> list:
    """Convert sub-agent movement dicts to MovimientoExtracto objects."""
    from concilia_engine.models import MovimientoExtracto
    from concilia_engine.normalizer import parse_date

    movimientos = []
    for i, m in enumerate(movs_data):
        try:
            fecha = parse_date(str(m.get("fecha", "")))
            if not fecha:
                continue
            valor = abs(float(m.get("valor", 0)))
            if valor == 0:
                continue
            movimientos.append(MovimientoExtracto(
                id=f"EXT-{i + 1:04d}",
                fecha=fecha,
                valor=valor,
                naturaleza=str(m.get("naturaleza", "debito")),
                descripcion=str(m.get("descripcion", "")).strip(),
            ))
        except Exception:
            continue
    return movimientos


def _build_info_extracto(info_data: dict):
    """Convert sub-agent info dict to InfoExtracto."""
    from concilia_engine.models import InfoExtracto
    from concilia_engine.normalizer import parse_date, parse_amount

    inicio = parse_date(str(info_data.get("periodo_inicio", "")))
    fin = parse_date(str(info_data.get("periodo_fin", "")))
    return InfoExtracto(
        banco=str(info_data.get("banco", "Desconocido")),
        numero_cuenta=str(info_data.get("numero_cuenta", "")),
        periodo_inicio=inicio,
        periodo_fin=fin,
        saldo_anterior=parse_amount(str(info_data.get("saldo_anterior", 0) or 0)),
        saldo_final=parse_amount(str(info_data.get("saldo_final", 0) or 0)),
    )


def _is_vision_configured(llm_config) -> bool:
    """Check if vision parsing is available with the given config."""
    api_key = llm_config.vision_api_key or llm_config.api_key
    model = llm_config.vision_model or llm_config.model
    return bool(api_key and model)


def _validate_pdf(file_bytes: bytes, config) -> dict | None:
    """Pre-validate a PDF before parsing. Returns error dict or None."""
    if len(file_bytes) == 0:
        return {
            "codigo": "ERR_ARCHIVO_VACIO",
            "mensaje": "El archivo PDF esta vacio (0 bytes).",
            "accion": "rechazar",
        }
    if file_bytes[:4] != b"%PDF":
        return {
            "codigo": "ERR_PDF_CORRUPTO",
            "mensaje": "El archivo no es un PDF valido (falta cabecera %PDF).",
            "accion": "rechazar",
        }
    if config:
        import pypdf
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            if reader.is_encrypted:
                # Some PDFs have encryption metadata but no password;
                # only reject if we can't decrypt with empty password
                try:
                    reader.decrypt("")
                except Exception:
                    return {
                        "codigo": "ERR_PDF_ENCRIPTADO",
                        "mensaje": "El PDF esta protegido con contrasena. Suba una version desbloqueada.",
                        "accion": "rechazar",
                    }
        except Exception:
            return {
                "codigo": "ERR_PDF_CORRUPTO",
                "mensaje": "El archivo PDF esta danado o corrupto y no se puede abrir.",
                "accion": "rechazar",
            }
    return None


# ──────────────────────────────────────────────────────────
# Post-extraction validation helpers (Fase C)
# ──────────────────────────────────────────────────────────

_BANKING_KEYWORDS = [
    "SALDO", "CUENTA", "DEBITO", "CREDITO", "EXTRACTO",
    "MOVIMIENTO", "AHORRO", "CORRIENTE", "BANCO",
    "PERIODO", "FECHA", "VALOR", "TRANSACCION",
    "INTERESES", "CONSIGNACION", "PAGO", "RETIRO",
]

_MATH_TOLERANCE = 1.0  # $1 tolerance for rounding differences


def _detect_no_es_extracto(texto: str, num_movimientos: int) -> dict | None:
    """Return error dict if text doesn't look like a bank statement."""
    if num_movimientos > 0:
        return None
    texto_upper = texto.upper()
    found = [kw for kw in _BANKING_KEYWORDS if kw in texto_upper]
    if len(found) < 2:
        return {
            "codigo": "ERR_NO_ES_EXTRACTO",
            "mensaje": "El documento no parece ser un extracto bancario valido.",
            "accion": "rechazar",
        }
    return None


def _check_math(info, movimientos) -> dict | None:
    """Check if saldo_anterior + sum(creditos) - sum(debitos) ≈ saldo_final."""
    if not info or not movimientos:
        return None
    if info.saldo_anterior == 0.0 and info.saldo_final == 0.0:
        return None

    total_credito = sum(m.valor for m in movimientos if m.naturaleza == "credito")
    total_debito = sum(m.valor for m in movimientos if m.naturaleza == "debito")
    expected_final = info.saldo_anterior + total_credito - total_debito
    diff = abs(expected_final - info.saldo_final)

    if diff > _MATH_TOLERANCE:
        return {
            "codigo": "ERR_CONCILIACION_MATEMATICA",
            "mensaje": (
                f"La suma de movimientos no cuadra con los saldos. "
                f"Saldo inicial: {info.saldo_anterior:,.2f}, "
                f"creditos: {total_credito:,.2f}, "
                f"debitos: {total_debito:,.2f}, "
                f"saldo esperado: {expected_final:,.2f}, "
                f"saldo real: {info.saldo_final:,.2f}, "
                f"diferencia: {diff:,.2f}"
            ),
            "accion": "revision_manual",
        }
    return None
