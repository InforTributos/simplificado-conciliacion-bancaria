"""Parser for Davivienda ― AHORROS ESPECIALES P. JURIDIC format.

Detected on davibanck.pdf which has 0 movements (summary-only statement).
"""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

_MES_ABR: dict[str, int] = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


class DavibanckParser(BankParser):
    """Parser for Davivienda AHORROS ESPECIALES P. JURIDIC statements.

    Particularities:
    - Month abbreviations: MAR, ABR, etc. (3-letter Spanish)
    - Amount: Colombian format (1.000.000,00)
    - Table columns: Fecha aplic, Fecha movto, Cod Ofc, Num Cheque, Descripcion, Monto, Saldo
    - May have 0 movements (summary-only statement)
    """

    banco_nombre = "davibanck"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "AHORROS ESPECIALES P. JURIDIC" in texto_upper

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        hint_year = self._extract_year(texto)
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            for m in re.finditer(
                r"(\d{1,2})\s+([A-Z]{3})\s+(.+?)\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})$",
                line,
            ):
                dia = int(m.group(1))
                mes_abr = m.group(2).upper()
                desc = m.group(3).strip()
                monto_str = m.group(4)
                balance_str = m.group(5)

                # Skip Saldo Inicial / Saldo Final rows
                desc_upper = desc.upper()
                if desc_upper in ("SALDO INICIAL", "SALDO FINAL"):
                    continue

                mes = _MES_ABR.get(mes_abr)
                if mes is None or hint_year is None:
                    continue

                try:
                    fecha = date(hint_year, mes, dia)
                except ValueError:
                    continue

                # Skip if no amount
                if monto_str in ("0,00", "0.00", "0", ""):
                    continue

                monto = parse_amount(monto_str, formato="co")
                if monto is None or monto == 0:
                    continue

                balance = parse_amount(balance_str, formato="co")
                naturaleza = self._detect_naturaleza(desc_upper)

                movimientos.append(MovimientoExtracto(
                    id=f"DVK-{seq:04d}",
                    fecha=fecha,
                    valor=abs(monto),
                    naturaleza=naturaleza,
                    descripcion=normalize_description(desc),
                ))
                seq += 1

        logger.info("Davibanck parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        cuenta = ""
        periodo_inicio = date.today()
        periodo_fin = date.today()
        saldo_anterior = 0.0
        saldo_final = 0.0

        for line in texto.split("\n"):
            # Account: AHORROS ESPECIALES P. JURIDIC 4252138379 ...
            m = re.search(r"AHORROS\s+ESPECIALES\s+P\.\s*JURIDIC\s+([\d]+)", line, re.IGNORECASE)
            if m and len(m.group(1)) >= 8:
                cuenta = normalize_cuenta(m.group(1))

            # Saldo Inicial: 1 MAR 2026 2.540.822.631,87
            m = re.search(
                r"Saldo\s+Inicial\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})",
                line, re.IGNORECASE,
            )
            if m:
                dia = int(m.group(1))
                mes = _MES_ABR.get(m.group(2).upper())
                year = int(m.group(3))
                if mes:
                    try:
                        periodo_inicio = date(year, mes, dia)
                    except ValueError:
                        pass
                v = parse_amount(m.group(4), formato="co")
                if v is not None:
                    saldo_anterior = v

            # Saldo Final: 31 MAR 2026 2.540.822.631,87
            m = re.search(
                r"Saldo\s+Final\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})",
                line, re.IGNORECASE,
            )
            if m:
                dia = int(m.group(1))
                mes = _MES_ABR.get(m.group(2).upper())
                year = int(m.group(3))
                if mes:
                    try:
                        periodo_fin = date(year, mes, dia)
                    except ValueError:
                        pass
                v = parse_amount(m.group(4), formato="co")
                if v is not None:
                    saldo_final = v

        return InfoExtracto(
            banco="Davivienda",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    @staticmethod
    def _extract_year(texto: str) -> int | None:
        m = re.search(r"\b20\d{2}\b", texto)
        if m:
            return int(m.group(0))
        return None

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
