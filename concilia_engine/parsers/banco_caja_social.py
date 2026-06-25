"""Parser for Banco Caja Social bank statements."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

# Spanish month abbreviations used in movement lines
_MES_ABR: dict[str, int] = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


class BancoCajaSocialParser(BankParser):
    """Parser for Banco Caja Social statements.

    Particularities:
    - Date: MMM DD (month abbreviation + day), year from header
    - Amount: US format (1,234,567.89)
    - Nature: balance-direction (primary) or keyword fallback
    - Columns: Fecha Transaccion Documento Lugar Debitos Creditos Saldos
    """

    banco_nombre = "banco_caja_social"
    invertir_lado = "extracto"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "BANCO CAJA SOCIAL" in texto_upper or "860007335" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        hint_year = self._extract_year(texto)
        prev_balance = self._extract_saldo_anterior(texto)
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Format: MMM DD DESCRIPTION ... AMOUNT BALANCE
            # Use finditer for concatenated lines
            for m in re.finditer(
                r"(\w{3})\s+(\d{1,2})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
                line,
            ):
                mes_abr = m.group(1).upper()
                dia = int(m.group(2))
                desc = m.group(3)
                valor_str = m.group(4)
                balance_str = m.group(5)

                mes = _MES_ABR.get(mes_abr)
                if mes is None or hint_year is None:
                    continue

                try:
                    fecha = date(hint_year, mes, dia)
                except ValueError:
                    continue

                valor = parse_amount(valor_str, formato="us")
                if valor is None or valor == 0:
                    continue

                descripcion = normalize_description(desc)

                # Nature: balance-direction primary, keyword fallback
                balance = parse_amount(balance_str, formato="us")
                if balance is not None and prev_balance is not None:
                    if balance > prev_balance:
                        naturaleza = "credito"
                    elif balance < prev_balance:
                        naturaleza = "debito"
                    else:
                        naturaleza = "credito" if "ABONO" in desc.upper() else "debito"
                    prev_balance = balance
                else:
                    naturaleza = "credito" if "ABONO" in desc.upper() else "debito"

                movimientos.append(MovimientoExtracto(
                    id=f"BCS-{seq:04d}",
                    fecha=fecha,
                    valor=abs(valor),
                    naturaleza=naturaleza,
                    descripcion=descripcion,
                ))
                seq += 1

        logger.info("Banco Caja Social parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        banco = "Banco Caja Social"
        cuenta = ""
        periodo_inicio = date.today()
        periodo_fin = date.today()
        saldo_anterior = 0.0
        saldo_final = 0.0

        for line in texto.split("\n"):
            line_s = line.strip()
            # Account: may be masked like "241**4*1*9**8*0515" — strip non-digits
            m = re.search(r"(\d[\d\*]{7,}\d)", line_s)
            if m:
                candidate = re.sub(r"\D", "", m.group(1))
                if len(candidate) >= 8:
                    cuenta = normalize_cuenta(candidate)

            # Period: "1 de Marzo a 31 de Marzo de 2026"
            m = re.search(
                r"(\d{1,2})\s+de\s+(\w+)\s+a\s+(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",
                line, re.IGNORECASE,
            )
            if m:
                dia_ini = int(m.group(1))
                mes_ini = _MES_NOMBRE.get(m.group(2).upper())
                dia_fin = int(m.group(3))
                mes_fin = _MES_NOMBRE.get(m.group(4).upper())
                year = int(m.group(5))
                if mes_ini and mes_fin:
                    periodo_inicio = date(year, mes_ini, dia_ini)
                    periodo_fin = date(year, mes_fin, dia_fin)

        # Extract saldos from the summary row (values may be on a separate line)
        # Pattern: "Saldo Anterior ... Nuevo Saldo\n<val1> <val2> <val3> <val4> <val5>"
        m = re.search(
            r"SALDO\s+ANTERIOR[^\n]*\n\s*([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="us")
            if v is not None:
                saldo_anterior = v
            v = parse_amount(m.group(5), formato="us")
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
    def _extract_year(texto: str) -> int | None:
        m = re.search(r"de\s+(\d{4})", texto)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _extract_saldo_anterior(texto: str) -> float | None:
        m = re.search(
            r"SALDO\s+ANTERIOR[^\n]*\n\s*([\d,]+\.\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="us")
            if v is not None:
                return v
        return None


_MES_NOMBRE: dict[str, int] = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4,
    "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
