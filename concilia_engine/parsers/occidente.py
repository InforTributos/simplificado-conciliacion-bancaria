"""Parser for Banco de Occidente statements (validated with Cartagena Jun 2025)."""

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


class OccidenteParser(BankParser):
    """Parser for Banco de Occidente statements.

    Particularities:
    - Date: day only (e.g., "03", "16"), infer month/year from FECHA DE CORTE header
    - Amount: US format (1,234,567.89)
    - Nature: separate DEBITOS/CREDITOS columns
    - Account: with dashes (830-96717-0)
    - Columns: DIA | TRANSACCION | IDENT. | DEBITOS | CREDITOS | SALDO
    """

    banco_nombre = "occidente"
    invertir_lado = "extracto"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        # Avoid false positive: "BANCO DE BOGOTA" may contain "OCCIDENTE"
        # in some table layouts.  Only match when it's truly Occidente.
        if "BANCO DE BOGOTA" in texto_upper or "BANCO DE BOGOTÁ" in texto_upper:
            return False
        return "OCCIDENTE" in texto_upper or "890300279" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        corte_year, corte_month = self._extract_corte_date(texto)
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        # Detect format variant from headers
        is_fiduciaria = "FIDUOCCIDENTE" in texto.upper()

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if is_fiduciaria:
                # Fiduciaria format: DD/MM CODE DESCRIPTION CITY OFFICE DOC AMOUNT BALANCE
                # Accept optional negative sign on amount. Use finditer to capture
                # multiple movements on concatenated lines (pdfplumber merges close rows).
                for m in re.finditer(
                    r"(\d{1,2}/\d{1,2})\s+(\d+)\s+(.+?)\s+(-?[\d,]+\.\d{2})\s+([\d,.\-()]+)",
                    line,
                ):
                    fecha_str = m.group(1)
                    desc = m.group(3)
                    valor_str = m.group(4)

                    fecha = parse_date(fecha_str, hint_year=corte_year)
                    if not fecha:
                        continue

                    valor = parse_amount(valor_str, formato="us")
                    if valor is None or valor == 0:
                        continue

                    naturaleza = self._detect_naturaleza(desc)
                    movimientos.append(MovimientoExtracto(
                        id=f"EXT-{seq:04d}",
                        fecha=fecha,
                        valor=abs(valor),
                        naturaleza=naturaleza,
                        descripcion=normalize_description(desc),
                    ))
                    seq += 1
                continue

            # Classic Occidente format: DD DESCRIPTION DEBITOS CREDITOS SALDO
            m = re.match(
                r"(\d{1,2})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]*\.?\d*)\s+([\d,]+\.\d{2})\s*$",
                line,
            )
            if not m:
                m = re.match(
                    r"(\d{1,2})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$",
                    line,
                )
                if m:
                    day_str = m.group(1)
                    desc = m.group(2)
                    continue
                continue

            day_str = m.group(1)
            desc = m.group(2)
            debito_str = m.group(3)
            credito_str = m.group(4)

            fecha = parse_date(day_str, hint_year=corte_year, hint_month=corte_month)
            if not fecha:
                continue

            debito = parse_amount(debito_str, formato="us") if debito_str.strip() else None
            credito = parse_amount(credito_str, formato="us") if credito_str.strip() else None

            if debito and debito > 0:
                valor = debito
                naturaleza = "debito"
            elif credito and credito > 0:
                valor = credito
                naturaleza = "credito"
            else:
                continue

            movimientos.append(MovimientoExtracto(
                id=f"EXT-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc),
            ))
            seq += 1

        logger.info("Occidente parser: extracted %d movements", len(movimientos))
        return movimientos

    def _detect_naturaleza(self, desc: str) -> str:
        """Detect nature from description keywords."""
        desc_upper = desc.upper()
        credito_kw = ["ABONO", "CONSIGNACION", "CREDITO", "INTERES", "RENDIMIENTO"]
        debito_kw = ["CARGO", "DEBITO", "RETIRO", "PAGO", "CHEQUE", "COMISION", "GMF"]
        for kw in credito_kw:
            if kw in desc_upper:
                return "credito"
        for kw in debito_kw:
            if kw in desc_upper:
                return "debito"
        return "debito"

    def extraer_info(self, texto: str) -> InfoExtracto:
        corte_year, corte_month = self._extract_corte_date(texto)
        cuenta = ""
        saldo_anterior = 0.0
        saldo_final = 0.0

        for line in texto.split("\n"):
            # Account: 830-96717-0
            m = re.search(r"(?:CUENTA|No\.?)\s*[:\s]*([\d]+-[\d]+-[\d]+)", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))

            # Saldo anterior — handles "SALDO ANTERIOR" and "Saldo Inicial"
            m = re.search(r"SALDO\s+(?:INICIAL|ANTERIOR)[:\s]*([\d,.\-()]+)", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_anterior = v

            # Saldo final
            m = re.search(r"SALDO\s+(?:FINAL|ACTUAL)[:\s]*([\d,.\-()]+)", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_final = v

        # Build period from corte date
        if corte_year and corte_month:
            periodo_inicio = date(corte_year, corte_month, 1)
            periodo_fin = date(corte_year, corte_month, _last_day(corte_year, corte_month))
        else:
            periodo_inicio = periodo_fin = date.today()

        return InfoExtracto(
            banco="Banco de Occidente",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _extract_corte_date(self, texto: str) -> tuple[int | None, int | None]:
        """Extract month and year from 'FECHA DE CORTE: DD/MM/YYYY' header
        or 'FECHA EXTRACTO Mes - Mes AAAA' variant."""
        # Standard format: FECHA DE CORTE: 30/06/2025
        m = re.search(r"FECHA\s+DE\s+CORTE[:\s]*(\d{1,2})[/-](\d{1,2})[/-](\d{4})", texto, re.IGNORECASE)
        if m:
            return int(m.group(3)), int(m.group(2))

        m = re.search(r"CORTE[:\s]*(\d{1,2})[/-](\d{1,2})[/-](\d{4})", texto, re.IGNORECASE)
        if m:
            return int(m.group(3)), int(m.group(2))

        # Fiduciaria format: FECHA EXTRACTO Febrero - Febrero 2025
        m = re.search(r"FECHA\s+EXTRACTO\s+(\w+)\s*-\s*\w+\s+(\d{4})", texto, re.IGNORECASE)
        if m:
            mes = MESES.get(m.group(1).upper())
            year = int(m.group(2))
            if mes:
                return year, mes

        return None, None


def _last_day(year: int, month: int) -> int:
    """Return last day of the given month."""
    import calendar
    return calendar.monthrange(year, month)[1]
