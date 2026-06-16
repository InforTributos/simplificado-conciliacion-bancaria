"""Parser for Banco GNB Sudameris / Fiduciaria La Previsora statements (regex only)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

CREDITO_KEYWORDS = ["NC", "NOTA CREDITO", "ABONO", "INTERESES", "RECAUDOS", "CONSIGNACION"]
DEBITO_KEYWORDS = ["PAGO", "DEBITO", "RETIRO", "CARGO", "CHEQUE", "GMF", "4X1000", "COMISION"]


class BancoGNBParser(BankParser):
    """Parser for Banco GNB Sudameris statements.

    Particularities:
    - Date: MM/DD (no year — extracted from header YYYY M DD format)
    - Amount: US format (1,234,567.89), single column
    - Nature: "NC" prefix in line = credito; keywords otherwise
    - Often fiduciary accounts (FIDUCIARIA LA PREVISORA)
    """

    banco_nombre = "banco_gnb"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return ("BCO GNB" in texto_upper) or ("BANCO GNB" in texto_upper) or ("GNB SUDAMERIS" in texto_upper)

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        year = self._extract_year(texto)
        if year is None:
            logger.warning("BancoGNB: could not extract year from header")
            return []

        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Format: MM/DD NC DESC... OFFICE AMOUNT BALANCE
            # Or:      MM/DD DESC... AMOUNT BALANCE (without NC)
            m = re.search(
                r"(\d{1,2})/(\d{1,2})\s+(?:NC\s+)?(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$",
                line,
            )
            if not m:
                continue

            mes, dia = int(m.group(1)), int(m.group(2))
            try:
                fecha = date(year, mes, dia)
            except ValueError:
                continue

            valor = parse_amount(m.group(4), formato="us")
            if not valor:
                continue

            desc_raw = m.group(3).strip()
            naturaleza = self._detect_naturaleza(line, desc_raw)

            movimientos.append(MovimientoExtracto(
                id=f"GNB-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc_raw),
            ))
            seq += 1

        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        texto_full = texto

        cuenta = ""
        for line in texto.split("\n"):
            m = re.search(r"(?:CUENTA|CUENTA\s+No|Cta\s+Cliente)\s*[:\s]*([\d]{6,})", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))
                break
        if not cuenta:
            m = re.search(r"Cuenta\s+(\d{8,})", texto_full)
            if m:
                cuenta = normalize_cuenta(m.group(1))

        saldo_anterior = _extract_amount(texto_full, r"0\.00\s+([\d,]+\.\d{2})\s*\n")

        # saldo_final: last movement's balance column (no "SALDO FINAL" label in GNB)
        saldo_final = 0.0
        balances = re.findall(
            r"\d{1,2}/\d{1,2}\s+(?:NC\s+)?.+?\s+[\d,]+\.\d{2}\s+([\d,]+\.\d{2})\s*$",
            texto_full,
            re.MULTILINE,
        )
        if balances:
            saldo_final = parse_amount(balances[-1], formato="us") or 0.0

        # Period: extract from "YYYY M DD" header date (single closing date)
        periodo_inicio = date.today()
        periodo_fin = date.today()
        m = re.search(r"(\d{4})\s+(\d{1,2})\s+(\d{1,2})", texto_full)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                periodo_fin = date(year, month, day)
                periodo_inicio = date(year, month, 1)
            except ValueError:
                pass

        return InfoExtracto(
            banco="Banco GNB Sudameris",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_year(self, texto: str) -> int | None:
        """Extract year from header line: '2026 3 31' (YYYY M DD)."""
        m = re.search(r"(\d{4})\s+\d{1,2}\s+\d{1,2}", texto)
        if m:
            return int(m.group(1))
        return None

    def _detect_naturaleza(self, line: str, descripcion: str) -> str:
        line_upper = line.upper()
        if re.search(r"\d{1,2}/\d{1,2}\s+NC\s+", line_upper):
            return "credito"

        desc_upper = descripcion.upper()
        for kw in CREDITO_KEYWORDS:
            if kw in desc_upper:
                return "credito"
        for kw in DEBITO_KEYWORDS:
            if kw in desc_upper:
                return "debito"
        return "debito"


def _extract_amount(texto: str, pattern: str) -> float:
    m = re.search(pattern, texto)
    if m:
        return parse_amount(m.group(1), formato="us") or 0.0
    return 0.0
