"""Parser for Scotiabank Colpatria bank statements."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)


class ColpatriaParser(BankParser):
    """Parser for Scotiabank Colpatria statements.

    Particularities:
    - Date: DD/MM/YYYY
    - Amount: Colombian format (1.234.567,89), negative sign for debits
    - Nature: determined by amount sign (negative = debito, positive = credito)
    - Columns: FECHA OFICINA No DOCUM DESCRIPCION MONTO SALDO
    """

    banco_nombre = "colpatria"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "SCOTIABANK" in texto_upper or "COLPATRIA" in texto_upper

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")
        prev_balance = self._extract_saldo_anterior(texto)
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Format: DD/MM/YYYY OFICINA DOC DESC MONTO SALDO
            # Use finditer for concatenated lines
            for m in re.finditer(
                r"(\d{1,2}/\d{1,2}/\d{4})\s+(.+?)\s+(-?[\d.,]+,\d{2})\s+(-?[\d.,]+,\d{2})",
                line,
            ):
                fecha_str = m.group(1)
                desc = m.group(2)
                valor_str = m.group(3)
                balance_str = m.group(4)

                fecha = parse_date(fecha_str)
                if not fecha:
                    continue

                valor = parse_amount(valor_str, formato="co")
                if valor is None or valor == 0:
                    continue

                descripcion = normalize_description(desc)

                # Nature by amount sign or balance direction
                if valor < 0:
                    naturaleza = "debito"
                else:
                    naturaleza = "credito"

                balance = parse_amount(balance_str, formato="co")
                if balance is not None:
                    prev_balance = balance

                movimientos.append(MovimientoExtracto(
                    id=f"COL-{seq:04d}",
                    fecha=fecha,
                    valor=abs(valor),
                    naturaleza=naturaleza,
                    descripcion=descripcion,
                ))
                seq += 1

        logger.info("Colpatria parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        banco = "Scotiabank Colpatria"
        cuenta = ""
        periodo_inicio = date.today()
        periodo_fin = date.today()
        saldo_anterior = 0.0
        saldo_final = 0.0

        for line in texto.split("\n"):
            # Account: No 4242213772
            m = re.search(r"No\s+(\d{6,})", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))

            # Period: 1 AL 31 MAR 2026
            m = re.search(r"(\d{1,2})\s+AL\s+(\d{1,2})\s+(\w{3})\s+(\d{4})", line, re.IGNORECASE)
            if m:
                dia_ini = int(m.group(1))
                dia_fin = int(m.group(2))
                mes = _MES_ABR.get(m.group(3).upper())
                year = int(m.group(4))
                if mes:
                    periodo_inicio = date(year, mes, dia_ini)
                    periodo_fin = date(year, mes, dia_fin)

        # Extract saldos from the summary row (values may be on a separate line)
        # Pattern: "SALDO ANTERIOR ... NUEVO SALDO\n<val1> <val2> <val3> <val4>"
        m = re.search(
            r"SALDO\s+ANTERIOR[^\n]*\n\s*([\d.,]+,\d{2})\s+([\d.,]+,\d{2})\s+([\d.,]+,\d{2})\s+([\d.,]+,\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="co")
            if v is not None:
                saldo_anterior = v
            v = parse_amount(m.group(4), formato="co")
            if v is not None:
                saldo_final = v

        return InfoExtracto(
            banco=banco,
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    @staticmethod
    def _extract_saldo_anterior(texto: str) -> float | None:
        m = re.search(
            r"SALDO\s+ANTERIOR.*?([\d.,]+,\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="co")
            if v is not None:
                return v
        return None


_MES_ABR: dict[str, int] = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}
