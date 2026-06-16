"""Parser for BBVA bank statements (validated with Valledupar Jan 2024).

Nature detection uses balance-direction (saldo sube = credito, saldo baja = debito)
as primary strategy, with keyword-based fallback when balance column is unavailable.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date, extract_cuenta_corta
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

_DEBITO_KEYWORDS = [
    "CARGO DOMI", "PAGO DE SERVICIOS", "VALOR PAGO", "PAGO PSE",
    "RETIRO", "DEBITO", "TRANSFERENCIA DE", "IMPUESTO", "IVA",
    "GMF", "4X1000", "COMISION",
]
_CREDITO_KEYWORDS = [
    "ABONO", "PAISOFICINA", "INTERESES GANADOS", "CONSIGNACION",
    "TRANSFERENCIA A", "CREDITO", "RENDIMIENTO", "NOTA CREDITO",
]


class BBVAParser(BankParser):
    """Parser for BBVA Colombia statements.

    Particularities:
    - Date: DD-MM-YYYY (complete)
    - Amount: US format (1,234,567.89), single column
    - Nature: balance-direction (primary) + keyword fallback
    - Account: long format with prefix (001309380200554490)
    """

    banco_nombre = "bbva"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        if "FONDO DE INVERSI" in texto_upper:
            return False
        return "BBVA" in texto_upper or "860003020" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")

        prev_balance = self._extract_saldo_anterior(texto)

        seq = 1
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Format 1: DD-MM-YYYY description amount balance
            m = re.search(
                r"(\d{2}-\d{2}-\d{4})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$",
                line,
            )
            if m:
                fecha_str, desc, monto_str, balance_str = m.group(1), m.group(2), m.group(3), m.group(4)
            else:
                # Format 2: DD-MM-YYYY description amount (no balance)
                m = re.search(
                    r"(\d{2}-\d{2}-\d{4})\s+(.+?)\s+([\d,]+\.\d{2})\s*$",
                    line,
                )
                if m:
                    fecha_str, desc, monto_str = m.group(1), m.group(2), m.group(3)
                    balance_str = None
                else:
                    continue

            fecha = parse_date(fecha_str)
            if not fecha:
                continue

            valor = parse_amount(monto_str, formato="us")
            if valor is None or valor == 0:
                continue

            descripcion = normalize_description(desc)

            if balance_str is not None and prev_balance is not None:
                balance = parse_amount(balance_str, formato="us")
                if balance is not None:
                    if balance > prev_balance:
                        naturaleza = "credito"
                    elif balance < prev_balance:
                        naturaleza = "debito"
                    else:
                        naturaleza = self._detect_naturaleza(desc)
                    prev_balance = balance
                else:
                    naturaleza = self._detect_naturaleza(desc)
            else:
                naturaleza = self._detect_naturaleza(desc)

            movimientos.append(MovimientoExtracto(
                id=f"EXT-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=descripcion,
            ))
            seq += 1

        logger.info("BBVA parser: extracted %d movements", len(movimientos))
        return movimientos

    @staticmethod
    def _extract_saldo_anterior(texto: str) -> float | None:
        m = re.search(
            r"SALDO\s+(?:CIERRE\s+MES\s+)?ANTERIOR[:\s]*([\d,.\-()]+)",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="us")
            if v is not None:
                return v
        return None

    def extraer_info(self, texto: str) -> InfoExtracto:
        banco = "BBVA"
        cuenta = ""
        periodo_inicio = date.today()
        periodo_fin = date.today()
        saldo_anterior = 0.0
        saldo_final = 0.0

        lines = texto.split("\n")
        for line in lines:
            # Account number
            m = re.search(r"(?:CUENTA|CTA|No\.?)\s*[:\s]*([\d]+)", line, re.IGNORECASE)
            if m and len(m.group(1)) > 8:
                cuenta = extract_cuenta_corta(m.group(1))

            # Period: "PERÍODO DESDE: 01-01-2024 HASTA: 31-01-2024" or "01-01-2024 AL 31-01-2024"
            m = re.search(r"(?:DESDE[:\s]*)?(\d{2}-\d{2}-\d{4})\s+(?:HASTA[:\s]+|AL?\s+)(\d{2}-\d{2}-\d{4})", line, re.IGNORECASE)
            if m:
                d1 = parse_date(m.group(1))
                d2 = parse_date(m.group(2))
                if d1:
                    periodo_inicio = d1
                if d2:
                    periodo_fin = d2

            # Balances — handles "SALDO ANTERIOR" and "SALDO CIERRE MES ANTERIOR"
            m = re.search(r"SALDO\s+(?:CIERRE\s+MES\s+)?ANTERIOR[:\s]*([\d,.\-()]+)", line, re.IGNORECASE)
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
            banco=banco,
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _detect_naturaleza(self, descripcion: str) -> str:
        desc_upper = descripcion.upper()
        if "DEV. " in desc_upper or desc_upper.startswith("DEV "):
            if "CARGO" in desc_upper:
                return "credito"
            if "ABONO" in desc_upper:
                return "debito"
            return "credito"
        for kw in _DEBITO_KEYWORDS:
            if kw in desc_upper:
                return "debito"
        for kw in _CREDITO_KEYWORDS:
            if kw in desc_upper:
                return "credito"
        return "debito"
