"""Parser for Banco Agrario de Colombia bank statements.

Detected on bancoAgrario.pdf (estado de cuenta corriente, 0 movimientos).
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

_MES_NOMBRE: dict[str, int] = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4,
    "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


class BancoAgrarioParser(BankParser):
    """Parser for Banco Agrario de Colombia statements.

    Particularities:
    - Period: full Spanish month name + year (e.g., MARZO 2026)
    - Amount: Colombian format (1.000.000,00)
    - Account: standalone numeric line (8-15 digits)
    - May have 0 movements (summary-only statement)
    """

    banco_nombre = "banco_agrario"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        if "FONDO DE INVERSI" in texto_upper:
            return False
        return (
            "BANCO AGRARIO" in texto_upper
            or "BANAGRARIO" in texto_upper
            or "bancoagrario.gov.co" in texto
        )

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        hint_year, hint_month = self._extract_period(texto)
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Try to match movement lines: DD/MM DESCRIPTION AMOUNT ...
            # Currently no movements in the sample PDF; this is a forward-looking pattern
            for m in re.finditer(
                r"(\d{1,2})/(\d{1,2})\s+(.+?)\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})",
                line,
            ):
                dia = int(m.group(1))
                mes = int(m.group(2))
                desc = m.group(3)
                monto_str = m.group(4)

                year = hint_year
                if year is None:
                    year = date.today().year

                try:
                    fecha = date(year, mes, dia)
                except ValueError:
                    continue

                monto = parse_amount(monto_str, formato="co")
                if monto is None or monto == 0:
                    continue

                desc_upper = desc.upper()
                naturaleza = self._detect_naturaleza(desc_upper)

                movimientos.append(MovimientoExtracto(
                    id=f"AGR-{seq:04d}",
                    fecha=fecha,
                    valor=abs(monto),
                    naturaleza=naturaleza,
                    descripcion=normalize_description(desc),
                ))
                seq += 1

        logger.info("Banco Agrario parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        cuenta = ""
        periodo_inicio = date.today()
        periodo_fin = date.today()
        saldo_anterior = 0.0
        saldo_final = 0.0

        hint_year, hint_month = self._extract_period(texto)
        if hint_year and hint_month:
            _, last_day = calendar.monthrange(hint_year, hint_month)
            periodo_inicio = date(hint_year, hint_month, 1)
            periodo_fin = date(hint_year, hint_month, last_day)

        for line in texto.split("\n"):
            line_s = line.strip()

            # Account: standalone numeric line (10-16 digits)
            m = re.match(r"^(\d{10,16})\s*$", line_s)
            if m and not line_s.startswith("$"):
                cuenta = normalize_cuenta(m.group(1))

            # Saldo: "SALDO ANTERIOR 0,00 SALDO ACTUAL 0,00" (single line)
            m = re.search(
                r"SALDO\s+ANTERIOR\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})\s+SALDO\s+ACTUAL\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})",
                line_s, re.IGNORECASE,
            )
            if m:
                v = parse_amount(m.group(1), formato="co")
                if v is not None:
                    saldo_anterior = v
                v = parse_amount(m.group(2), formato="co")
                if v is not None:
                    saldo_final = v

        return InfoExtracto(
            banco="Banco Agrario",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    @staticmethod
    def _extract_period(texto: str) -> tuple[int | None, int | None]:
        """Extract month-year from 'MARZO 2026' header."""
        for nom, num in _MES_NOMBRE.items():
            m = re.search(rf"{nom}\s+(\d{{4}})", texto, re.IGNORECASE)
            if m:
                return int(m.group(1)), num
        return None, None

    @staticmethod
    def _detect_naturaleza(desc_upper: str) -> str:
        credit_kw = ["ABONO", "CREDITO", "DEPOSITO", "CONSIGNACION", "INTERES", "RENDIMIENTO"]
        debit_kw = ["CARGO", "DEBITO", "RETIRO", "PAGO", "CHEQUE", "COMISION", "GMF"]
        for kw in credit_kw:
            if kw in desc_upper:
                return "credito"
        for kw in debit_kw:
            if kw in desc_upper:
                return "debito"
        return "debito"
