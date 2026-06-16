"""Parser for Banco de Bogota statements (validated with Monteria Feb 2025)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

# Month name to number mapping (Spanish)
MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4,
    "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


class BogotaParser(BankParser):
    """Parser for Banco de Bogota statements.

    Particularities:
    - Date: DD/MM without year, infer year from header
    - Amount: US format (1,234,567.89)
    - Columns: Fecha | CodTrans | Descripcion | Ciudad | Oficina/Canal | Documento | Valor | Saldo
    - High volume: validated with 949 movements (13 pages)
    - Documento column preserved as referencia for matching
    """

    banco_nombre = "bogota"

    def puede_parsear(self, texto: str) -> bool:
        texto_lower = texto.lower()
        return "bancodebogota" in texto_lower or "banco de bogota" in texto_lower or "860002964" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        hint_year = self._extract_year(texto)
        movimientos = []
        lines = texto.split("\n")
        prev_balance = self._extract_saldo_anterior(texto)
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Match: DD/MM CodTrans Description ... Documento Valor Saldo
            # Use finditer to capture multiple movements on concatenated lines
            # (pdfplumber may merge close rows from multi-column layouts).
            matched = False
            for m in re.finditer(
                r"(\d{1,2}/\d{1,2})\s+(\S+)\s+(.+?)\s+(-?[\d,]+\.\d{2})\s+(-?[\d,.\-()]+)",
                line,
            ):
                matched = True
                fecha_str = m.group(1)
                desc = m.group(3)
                valor_str = m.group(4)
                balance_str = m.group(5)
                referencia = self._extract_documento(desc)

                fecha = parse_date(fecha_str, hint_year=hint_year)
                if not fecha:
                    continue

                valor = parse_amount(valor_str, formato="us")
                if valor is None or valor == 0:
                    continue

                descripcion = normalize_description(desc)
                naturaleza = self._resolve_naturaleza(desc, valor, balance_str, prev_balance)

                balance = parse_amount(balance_str, formato="us")
                if balance is not None:
                    prev_balance = balance

                movimientos.append(MovimientoExtracto(
                    id=f"EXT-{seq:04d}",
                    fecha=fecha,
                    valor=abs(valor),
                    naturaleza=naturaleza,
                    descripcion=descripcion,
                    referencia=referencia,
                ))
                seq += 1

            if matched:
                continue

            # Fallback: simpler DD/MM ... Valor Saldo
            for m in re.finditer(
                r"(\d{1,2}/\d{1,2})\s+(.+?)\s+(-?[\d,]+\.\d{2})\s+(-?[\d,.\-()]+)",
                line,
            ):
                fecha_str = m.group(1)
                desc = m.group(2)
                valor_str = m.group(3)
                balance_str = m.group(4)

                fecha = parse_date(fecha_str, hint_year=hint_year)
                if not fecha:
                    continue

                valor = parse_amount(valor_str, formato="us")
                if valor is None or valor == 0:
                    continue

                descripcion = normalize_description(desc)
                naturaleza = self._resolve_naturaleza(desc, valor, balance_str, prev_balance)

                balance = parse_amount(balance_str, formato="us")
                if balance is not None:
                    prev_balance = balance

                movimientos.append(MovimientoExtracto(
                    id=f"EXT-{seq:04d}",
                    fecha=fecha,
                    valor=abs(valor),
                    naturaleza=naturaleza,
                    descripcion=descripcion,
                ))
                seq += 1

        logger.info("Bogota parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        hint_year = self._extract_year(texto)
        cuenta = ""
        saldo_anterior = 0.0
        saldo_final = 0.0
        periodo_inicio = date.today()
        periodo_fin = date.today()

        for line in texto.split("\n"):
            # Account: "Cuenta Número: X", "Cuenta: X", "CTA: X", "No. X"
            m = re.search(r"(?:CUENTA|CTA)\s*(?:N[UÚ]MERO\s*)?[:\s]+(\d{6,})", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))
            if not cuenta:
                m = re.search(r"No\.?\s*[:\s]*(\d{6,})", line, re.IGNORECASE)
                if m:
                    cuenta = normalize_cuenta(m.group(1))

            # Period: "Febrero - Febrero 2025" or "01/02 - 28/02"
            m = re.search(r"(\w+)\s*-\s*(\w+)\s+(\d{4})", line)
            if m:
                mes_inicio = MESES.get(m.group(1).upper())
                mes_fin = MESES.get(m.group(2).upper())
                year = int(m.group(3))
                if mes_inicio and mes_fin:
                    periodo_inicio = date(year, mes_inicio, 1)
                    import calendar
                    periodo_fin = date(year, mes_fin, calendar.monthrange(year, mes_fin)[1])

            # Saldos — handles "SALDO ANTERIOR" and "Saldo Inicial"
            m = re.search(r"SALDO\s+(?:INICIAL|ANTERIOR)[:\s]*([\d,.\-()]+)", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_anterior = v

            m = re.search(r"SALDO\s+(?:FINAL|ACTUAL)[:\s]*([\d,.\-()]+)", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_final = v

        return InfoExtracto(
            banco="Banco de Bogota",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _extract_year(self, texto: str) -> int | None:
        """Extract year from header text."""
        for mes_name, _num in MESES.items():
            m = re.search(rf"{mes_name}\s+(\d{{4}})", texto, re.IGNORECASE)
            if m:
                return int(m.group(1))
        # Fallback: any 4-digit year in first 20 lines
        for line in texto.split("\n")[:20]:
            m = re.search(r"\b(20\d{2})\b", line)
            if m:
                return int(m.group(1))
        return None

    def _extract_documento(self, desc: str) -> str | None:
        """Extract document/reference number from description."""
        m = re.search(r"\b(\d{6,})\b", desc)
        if m:
            return m.group(1)
        return None

    def _detect_naturaleza(self, desc: str, valor: float) -> str:
        """Detect nature from description keywords (fallback)."""
        desc_upper = desc.upper()
        credito_kw = ["ABONO", "CONSIGNACION", "CREDITO", "INTERES", "RENDIMIENTO", "NOTA CR"]
        for kw in credito_kw:
            if kw in desc_upper:
                return "credito"
        debito_kw = ["CARGO", "DEBITO", "RETIRO", "PAGO", "CHEQUE", "COMISION", "GMF", "NOTA DB"]
        for kw in debito_kw:
            if kw in desc_upper:
                return "debito"
        return "debito"  # Default

    @staticmethod
    def _extract_saldo_anterior(texto: str) -> float | None:
        m = re.search(
            r"SALDO\s+(?:INICIAL|ANTERIOR)[:\s]*([\d,.\-()]+)",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="us")
            if v is not None:
                return v
        return None

    def _resolve_naturaleza(
        self, desc: str, valor: float, balance_str: str | None, prev_balance: float | None,
    ) -> str:
        """Determine nature: balance-direction (primary) or keyword (fallback)."""
        if balance_str is not None and prev_balance is not None:
            balance = parse_amount(balance_str, formato="us")
            if balance is not None and balance != prev_balance:
                return "credito" if balance > prev_balance else "debito"
        return self._detect_naturaleza(desc, valor)
