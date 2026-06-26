"""Parser for Banco Popular bank statements (regex only, no LLM).

Banco Popular PDFs have a typewriter layout that pdfplumber extracts with
garbled column spacing.  Amounts use US format but the decimal part is
separated by a space (e.g. ``885,970 45`` for $885,970.45).
"""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

# Split amounts: integer-part (comma thousands or single digit like "0")  SPACE  decimal-cents (2 digits)
# NOTE: changed {2,} to + so zero amounts ("0 00") are captured — without this,
# lines with a single-digit integer in the debit/credit column shift the column
# assignment and the date "MM DD" prefix gets misidentified as an amount.
_AMOUNT_RE = re.compile(r"\b([\d,]+)\s+(\d{2})\b")


class BancoPopularParser(BankParser):
    """Parser for Banco Popular statements.

    Particularities:
    - Date: MM DD (no year — extracted from header YYYY/MM/DD)
    - Amount: US format with space-separated decimal (``885,970 45``)
    - Layout: separate DEBITO and CREDITO columns
    - Nature: determined by which column (debito or credito) has a non-zero value
    """

    banco_nombre = "banco_popular"
    invertir_lado = "extracto"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return (
            ("POPULAR" in texto_upper and ("BANCO" in texto_upper or "bancopopular" in texto.lower()))
            or "860007738" in texto.replace(".", "").replace(" ", "")
        )

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        logger.info("BancoPopular v2: usando parser corregido (fecha antes de amounts, regex {1,})")
        year = self._extract_year(texto)
        if year is None:
            logger.warning("BancoPopular: could not extract year from header")
            return []

        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header / footer / summary lines
            if self._is_skip_line(line):
                continue

            mov = self._parse_line(line, year, seq)
            if mov:
                movimientos.append(mov)
                seq += 1

        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        texto_full = texto

        cuenta = ""
        m = re.search(r"Cuenta\s+([\d\-]+)", texto)
        if m:
            cuenta = normalize_cuenta(m.group(1).replace("-", ""))

        saldo_anterior = 0.0
        saldo_final = 0.0
        m = re.search(
            r"Saldo\s+anterior[^\d]*([\d,]+)\s+(\d{2})\s+.*Saldo\s+final[^\d]*([\d,]+)\s+(\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            sa_int, sa_dec = m.group(1), m.group(2)
            sf_int, sf_dec = m.group(3), m.group(4)
            saldo_anterior = parse_amount(f"{sa_int}.{sa_dec}", formato="us") or 0.0
            saldo_final = parse_amount(f"{sf_int}.{sf_dec}", formato="us") or 0.0

        periodo_inicio = None
        periodo_fin = None
        m = re.search(r"Desde\s+(\d{4}/\d{2}/\d{2})\s+Hasta\s+(\d{4}/\d{2}/\d{2})", texto, re.IGNORECASE)
        if m:
            from concilia_engine.normalizer import parse_date
            periodo_inicio = parse_date(m.group(1))
            periodo_fin = parse_date(m.group(2))

        return InfoExtracto(
            banco="Banco Popular",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio or date.today(),
            periodo_fin=periodo_fin or date.today(),
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_year(self, texto: str) -> int | None:
        m = re.search(r"Desde\s+(\d{4})/", texto)
        if m:
            return int(m.group(1))
        m = re.search(r"Fecha\s+de\s+Corte\s*\n?.*?\b(20\d{2})\b", texto)
        if m:
            return int(m.group(1))
        return None

    def _is_skip_line(self, line: str) -> bool:
        skip_kw = [
            "SALDO ANTERIOR", "SALDO FINAL", "PAGINA", "TOTAL",
            "DETALLE DE TRANSACCIONES", "FECHA HORA OFICINA",
            "RETIROS DEBITOS", "DEPOSITOS CREDITOS",
        ]
        line_upper = line.upper()
        for kw in skip_kw:
            if kw in line_upper:
                return True
        return False

    def _parse_line(self, line: str, year: int, seq: int) -> MovimientoExtracto | None:
        # Date: MM DD at the start of the line — extract FIRST so the date
        # prefix is never misidentified as a monetary amount.
        m = re.match(r"(\d{2})\s+(\d{2})\b", line)
        if not m:
            return None
        mes, dia = int(m.group(1)), int(m.group(2))
        try:
            fecha = date(year, mes, dia)
        except ValueError:
            return None

        # Search for amounts ONLY in the rest of the line (after the date prefix).
        # This prevents "01 16" (MM DD) from being captured as an amount, which
        # would shift the débito/crédito/saldo column assignment.
        rest = line[m.end():]
        amounts = _AMOUNT_RE.findall(rest)
        if len(amounts) < 3:
            return None

        # Last 3 amounts: debito, credito, saldo
        debito_int, debito_dec = amounts[-3]
        credito_int, credito_dec = amounts[-2]
        # saldo_int, saldo_dec = amounts[-1]  # not needed for movement

        debito_val = parse_amount(f"{debito_int}.{debito_dec}", formato="us") or 0.0
        credito_val = parse_amount(f"{credito_int}.{credito_dec}", formato="us") or 0.0

        # If both are zero, skip
        if debito_val == 0 and credito_val == 0:
            return None

        # If both are non-zero, take the larger one (ambiguous case)
        if debito_val > 0 and credito_val > 0:
            if debito_val >= credito_val:
                naturaleza = "debito"
                valor = debito_val
            else:
                naturaleza = "credito"
                valor = credito_val
        elif debito_val > 0:
            naturaleza = "debito"
            valor = debito_val
        else:
            naturaleza = "credito"
            valor = credito_val

        # Description: everything between date and the first of the last 3 amounts
        desc_start_pos = m.end()
        first_amount_start = rest.find(f"{amounts[-3][0]} {amounts[-3][1]}")
        if first_amount_start == -1:
            return None
        desc_raw = rest[:first_amount_start].strip()

        return MovimientoExtracto(
            id=f"POP-{seq:04d}",
            fecha=fecha,
            valor=abs(valor),
            naturaleza=naturaleza,
            descripcion=normalize_description(desc_raw),
        )
