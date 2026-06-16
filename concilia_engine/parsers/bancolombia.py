"""Parser for Bancolombia statements.

Real format (from BANCOLOMBIA.pdf):
    Header: ESTADO DE CUENTA DESDE: YYYY/MM/DD HASTA: YYYY/MM/DD CUENTA DE AHORROS NÚMERO XXXXXXXXXXX
    Movements (page 2): DD/MM DESCRIPCION SUCURSAL DCTO. VALOR SALDO
    Date: DD/MM in movements, year from header
    Amount: US format (1,234.56) — comma = thousands, dot = decimal
    Nature: detected from keywords (CONSIGNACION=credito, PAGO=debito, etc.)
"""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

DEBITO_KW = ["DEBITO", "CARGO", "RETIRO", "PAGO", "COMPRA", "GMF", "IVA", "COMISION", "SEGURO", "CUOTA"]
CREDITO_KW = ["CREDITO", "ABONO", "CONSIGNACION", "TRANSFERENCIA", "TRASPASO", "RENDIMIENTO", "INTERES", "REINTEGRO"]


class BancolombiaParser(BankParser):
    banco_nombre = "bancolombia"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "BANCOLOMBIA" in texto_upper or "890903938" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        # Extract year from header: "DESDE: YYYY/MM/DD HASTA: YYYY/MM/DD"
        year = self._extract_year(texto)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header lines, all-caps, totals, etc.
            if self._is_header_or_total(line):
                continue

            # Match: DD/MM description... amount,amount.xx balance,amount.xx
            # Format: "01/03 CONSIGNACION NACIONAL OFI001 12345 500,000.00 15,500,000.00"
            m = re.search(
                r"(\d{1,2})/(\d{1,2})\s+"
                r"(.+?)\s+"
                r"([\d,]+\.\d{2})\s+"
                r"([\d,]+\.\d{2})\s*$",
                line,
            )
            if not m:
                # Try without final balance: DD/MM description amount.xx
                m = re.search(
                    r"(\d{1,2})/(\d{1,2})\s+(.+?)\s+([\d,]+\.\d{2})\s*$",
                    line,
                )
                if not m:
                    continue
                dia, mes, desc, valor_str, saldo_str = m.group(1), m.group(2), m.group(3), m.group(4), ""
            else:
                dia, mes, desc, valor_str, saldo_str = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)

            if not year:
                continue

            try:
                fecha = date(int(year), int(mes), int(dia))
            except ValueError:
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

        logger.info("Bancolombia parser: extracted %d movements", len(movimientos))
        return movimientos

    def _extract_year(self, texto: str) -> str | None:
        """Extract year from header: DESDE: YYYY/MM/DD HASTA: YYYY/MM/DD"""
        m = re.search(r"DESDE\s*:\s*(\d{4})/\d{2}/\d{2}", texto, re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    def _is_header_or_total(self, line: str) -> bool:
        """Skip header lines, page markers, totals."""
        upper = line.upper()
        # All-caps with few digits = header
        non_space = line.replace(" ", "")
        if len(non_space) > 0:
            alpha_ratio = sum(1 for c in non_space if c.isalpha()) / len(non_space)
            digits_ratio = sum(1 for c in non_space if c.isdigit()) / max(len(non_space), 1)
            if alpha_ratio > 0.7 and digits_ratio < 0.1:
                return True
        # Total/summary markers
        skip_kw = ["TOTAL MOVIMIENTOS", "SALDO ANTERIOR", "SALDO FINAL", "PAGINA", "PÁGINA", "ESTADO DE CUENTA"]
        for kw in skip_kw:
            if kw in upper:
                return True
        return False

    def _detect_naturaleza(self, desc: str) -> str:
        """Detect nature from description keywords."""
        upper = desc.upper()
        for kw in CREDITO_KW:
            if kw in upper:
                return "credito"
        return "debito"

    def extraer_info(self, texto: str) -> InfoExtracto:
        cuenta = ""
        saldo_anterior = 0.0
        saldo_final = 0.0
        periodo_inicio = date.today()
        periodo_fin = date.today()

        lines = texto.split("\n")

        for line in lines:
            # Cuenta: "NÚMERO 17500006667" or "CUENTA DE AHORROS ... NÚMERO XXXX"
            m = re.search(r"(?:CUENTA|NUMERO|NÚMERO|No\.?)\s*[#:\s]*([\d]{8,})", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))

            # Periodo: "DESDE: 2026/02/28 HASTA: 2026/03/31"
            m = re.search(r"DESDE\s*:\s*(\d{4}/\d{2}/\d{2})\s+HASTA\s*:\s*(\d{4}/\d{2}/\d{2})", line, re.IGNORECASE)
            if m:
                d1 = parse_date(m.group(1))
                d2 = parse_date(m.group(2))
                if d1:
                    periodo_inicio = d1
                if d2:
                    periodo_fin = d2

            # Saldos
            m = re.search(r"SALDO\s+ANTERIOR[:\s]*([\d,]+\.\d{2})", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_anterior = v

            m = re.search(r"SALDO\s+(?:FINAL|ACTUAL|DISPONIBLE)[:\s]*([\d,]+\.\d{2})", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_final = v

        return InfoExtracto(
            banco="Bancolombia",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )
