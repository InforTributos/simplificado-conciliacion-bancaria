"""Parser for Davivienda bank statements (validated with ENERO/2026 extract)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

# Spanish month names → number
MESES: dict[str, int] = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4,
    "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


class DaviviendaParser(BankParser):
    """Parser for Davivienda statements.

    Actual format (as extracted by pdfplumber):
      DD MM $amt+ DOC DESCRIPCION OFICINA
    - DD: day, MM: month (no year on each line)
    - $amt+: credit (amount in US format with $ prefix and + suffix)
    - $amt-: debit (rare, not seen in test data)
    - DOC: document / transaction code
    - DESCRIPCION: movement description
    - OFICINA: office name (free text, may have spaces)
    """

    banco_nombre = "davivienda"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "DAVIVIENDA" in texto_upper or "860034313" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        hint_year, hint_month = self._extract_month_year(texto)
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Match: DD MM $AMT[+/-] DOC DESCRIPTION...
            m = re.match(
                r"(\d{1,2})\s+(\d{1,2})\s+\$([\d,]+\.\d{2})\s*([+-])\s+(\d+)\s+(.+)",
                line,
            )
            if not m:
                continue

            day = int(m.group(1))
            month = int(m.group(2))
            amount_str = m.group(3)
            sign = m.group(4)
            doc = m.group(5)
            desc = m.group(6)

            fecha = _safe_date(hint_year, month, day)
            if not fecha:
                continue

            valor = parse_amount(amount_str, formato="us")
            if valor is None or valor == 0:
                continue

            naturaleza = "credito" if sign == "+" else "debito"

            movimientos.append(MovimientoExtracto(
                id=f"EXT-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc),
            ))
            seq += 1

        logger.info("Davivienda parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        hint_year, hint_month = self._extract_month_year(texto)
        cuenta = ""
        saldo_anterior = 0.0
        saldo_final = 0.0

        for line in texto.split("\n"):
            # Account number: "026600165034"
            m = re.search(r"(?:CUENTA\s+DE\s+AHORROS)\s*\n\s*([\d]+)", line, re.IGNORECASE)
            if not m:
                m = re.search(r"^(\d{8,15})\s*$", line.strip())
            if m and len(m.group(1)) >= 8 and not line.startswith("$"):
                cuenta = normalize_cuenta(m.group(1))

            # Saldo anterior
            m = re.search(r"SaldoAnterior\s+\$?([\d,]+\.\d{2})", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_anterior = v

            # Nuevo saldo
            m = re.search(r"NuevoSaldo\s+\$?([\d,]+\.\d{2})", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_final = v

        if hint_year and hint_month:
            import calendar
            periodo_inicio = date(hint_year, hint_month, 1)
            periodo_fin = date(hint_year, hint_month, calendar.monthrange(hint_year, hint_month)[1])
        else:
            periodo_inicio = periodo_fin = date.today()

        return InfoExtracto(
            banco="Davivienda",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _extract_month_year(self, texto: str) -> tuple[int | None, int | None]:
        """Extract month and year from 'INFORMEDELMES:ENERO/2026' header."""
        m = re.search(r"INFORME\s*DEL?\s*MES\s*:\s*(\w+)\s*/\s*(\d{4})", texto, re.IGNORECASE)
        if m:
            mes = MESES.get(m.group(1).upper())
            year = int(m.group(2))
            return year, mes
        return None, None


def _safe_date(year: int | None, month: int, day: int) -> date | None:
    """Create a date object, returning None for invalid dates."""
    if year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None
