"""Parser for AV Villas / Rentavillas bank statements (regex only, no LLM).

AV Villas PDFs may have encoding corruption where every character in header
text is doubled (``EEXXTTRRAACCTTOO`` → ``EXTRACTO``).  Movement lines are not affected.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)


def _deduplicate(texto: str) -> str:
    """Remove character doubling (PDF encoding artifact).

    Some AV Villas PDFs double every letter and certain punctuation
    (``.``, ``:``) in header text.  Digits and other chars are left intact
    so that movement amounts are not corrupted.
    """
    _DEDUP_CHARS = set(".:")
    result = []
    i = 0
    while i < len(texto):
        ch = texto[i]
        result.append(ch)
        if (ch.isalpha() or ch in _DEDUP_CHARS) and i + 1 < len(texto) and texto[i + 1] == ch:
            i += 2  # skip the duplicate
        else:
            i += 1
    return "".join(result)


def _full_dedup_line(line: str) -> str:
    """Deduplicate every non-space character on a single doubled line.

    Used for the PERIODO line where digits and slashes are also doubled.
    Collapses runs of even length by half (doubling artifact);
    odd runs are kept as-is (genuine repeats like ``777``).
    """
    result = []
    i = 0
    while i < len(line):
        ch = line[i]
        if not ch.isspace():
            j = i + 1
            while j < len(line) and line[j] == ch:
                j += 1
            run = j - i
            if run % 2 == 0:
                result.extend([ch] * (run // 2))  # collapse pairs
            else:
                result.extend([ch] * run)  # odd run, keep
            i = j
        else:
            result.append(ch)
            i += 1
    return "".join(result)


class AvVillasParser(BankParser):
    """Parser for AV Villas / Rentavillas statements.

    Particularities:
    - Date: YYYY/MM/DD (ISO-like with slashes)
    - Amount: US format (1,234,567.89), prefixed with ``$``
    - May have encoding corruption (doubled characters in headers)
    - Simple format: DATE | DESCRIPTION | AMOUNT | BALANCE
    """

    banco_nombre = "avvillas"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        if "AV VILLAS" in texto_upper or "RENTAVILLAS" in texto_upper:
            return True
        dedup = _deduplicate(texto).upper()
        return "AV VILLAS" in dedup or "RENTAVILLAS" in dedup

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        # AvVillas: try original text first, then deduplicated
        return self._parse_text(texto) or self._parse_text(_deduplicate(texto))

    def _parse_text(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Format: YYYY/MM/DD DESC... $VALOR $SALDO
            m = re.search(
                r"(\d{4}/\d{2}/\d{2})\s+(.+?)\s+\$\s*([\d,]+\.\d{2})\s+\$\s*([\d,]+\.\d{2})\s*$",
                line,
            )
            if not m:
                continue

            fecha = parse_date(m.group(1))
            if fecha is None:
                continue

            valor = parse_amount(m.group(3), formato="us")
            if not valor:
                continue

            desc_raw = m.group(2).strip()
            naturaleza = self._detect_naturaleza(desc_raw)

            movimientos.append(MovimientoExtracto(
                id=f"AVV-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc_raw),
            ))
            seq += 1

        return movimientos or None

    def extraer_info(self, texto: str) -> InfoExtracto:
        # Light dedup for labels (letters only) — preserves numeric values intact
        texto_light = _deduplicate(texto)
        if "MOVIMIENTO DIARIO" not in texto_light.upper():
            texto_light = _deduplicate(texto_light)

        cuenta = ""
        m = re.search(r"(?:CUENTA|DEP[OÓ]SITO)\s+N[Oo].\s*[:\s]*([\d\-]+)", texto_light)
        if m:
            cuenta = normalize_cuenta(m.group(1).replace("-", ""))

        # Period: the PERIODO line has EVERYTHING doubled (digits + slashes too).
        # Find it in raw text, apply full dedup, extract dates.
        periodo_inicio = None
        periodo_fin = None
        for line in texto.split("\n"):
            if re.search(r"P+E+R+[IÍ]+O+D+O+", line, re.IGNORECASE):
                line_dedup = _full_dedup_line(line)
                m = re.search(r"PER[IÍ]ODO\s+(\d{4}/\d{2}/\d{2})\s+A+\s+(\d{4}/\d{2}/\d{2})", line_dedup, re.IGNORECASE)
                if m:
                    periodo_inicio = parse_date(m.group(1))
                    periodo_fin = parse_date(m.group(2))
                    break

        # Saldos: light dedup has readable labels + intact numeric values
        saldo_anterior = _extract_amount(texto_light, r"saldo\s+inicial\s*:\s*\$?\s*([\d,]+\.\d{2})", re.IGNORECASE)
        saldo_final = _extract_amount(texto_light, r"saldo\s+final\s*(?:per[íi]odo)?\s*:\s*\$?\s*([\d,]+\.\d{2})", re.IGNORECASE)

        return InfoExtracto(
            banco="AV Villas",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio or date.today(),
            periodo_fin=periodo_fin or date.today(),
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _detect_naturaleza(self, descripcion: str) -> str:
        desc_upper = descripcion.upper()
        if any(kw in desc_upper for kw in ["INTERESES", "RENDIM", "ABONO", "CREDITO", "CONSIGNACION"]):
            return "credito"
        if any(kw in desc_upper for kw in ["DEBITO", "CARGO", "RETIRO", "PAGO", "GMF", "COMISION"]):
            return "debito"
        return "debito"


def _extract_amount(texto: str, pattern: str, flags=0) -> float:
    m = re.search(pattern, texto, flags)
    if m:
        return parse_amount(m.group(1), formato="us") or 0.0
    return 0.0
