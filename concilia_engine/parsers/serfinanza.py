"""Parser for Banco Serfinanza bank statements (regex only, no LLM)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

CREDITO_KEYWORDS = ["INTERESES", "ABONO", "NC", "NOTA CREDITO", "CONSIGNACION", "RENDIMIENTO"]
DEBITO_KEYWORDS = ["ACH", "TRANSFERENCIA", "PAGO", "CARGO", "DEBITO", "RETIRO", "GMF", "4X1000", "COMISION"]


class SerfinanzaParser(BankParser):
    """Parser for Banco Serfinanza S.A. statements.

    Particularities:
    - Date: DD/MM/YYYY (complete, on each movement line)
    - Amount: US format (1,234,567.89), single column
    - Nature: inferred from keywords in description
    """

    banco_nombre = "serfinanza"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "SERFINANZA" in texto_upper

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Format: DD/MM/YYYY DESC... OFFICE VALOR SALDO
            m = re.search(
                r"(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$",
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
                id=f"SER-{seq:04d}",
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
            m = re.search(r"(?:CUENTA|CTA|No\.?)\s*(?:DE\s+AHORROS)?\s*[:\s]*([\d]{6,})", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))
                break
        # Fallback: standalone numeric line (8-16 digits) — account may be on its own line
        if not cuenta:
            for line in texto.split("\n"):
                m = re.match(r"^(\d{8,16})\s*$", line.strip())
                if m:
                    candidate = normalize_cuenta(m.group(1))
                    if len(candidate) >= 6:
                        cuenta = candidate
                        break

        saldo_anterior = _extract_amount(texto_full, r"SALDO\s+ANTERIOR[:\s]*([\d,]+\.\d{2})")
        saldo_final = _extract_amount(texto_full, r"SALDO\s+ACTUAL[:\s]*([\d,]+\.\d{2})")

        periodo_inicio = None
        periodo_fin = None
        for line in texto.split("\n"):
            m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})", line)
            if m:
                periodo_inicio = parse_date(m.group(1))
                periodo_fin = parse_date(m.group(2))
                break

        return InfoExtracto(
            banco="Banco Serfinanza",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio or date.today(),
            periodo_fin=periodo_fin or date.today(),
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _detect_naturaleza(self, descripcion: str) -> str:
        desc_upper = descripcion.upper()
        for kw in DEBITO_KEYWORDS:
            if kw in desc_upper:
                return "debito"
        for kw in CREDITO_KEYWORDS:
            if kw in desc_upper:
                return "credito"
        return "debito"


def _extract_amount(texto: str, pattern: str) -> float:
    m = re.search(pattern, texto)
    if m:
        return parse_amount(m.group(1), formato="us") or 0.0
    return 0.0
