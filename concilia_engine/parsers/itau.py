"""Parser for Itaú Colombia S.A. statements (Cuenta de Ahorros)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

MESES: dict[str, int] = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4,
    "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


class ItauParser(BankParser):
    """Parser for Itaú Colombia statements.

    Particularities:
    - Date: day only (01-31), month/year inferred from header "DD/MM/YYYY AL DD/MM/YYYY"
    - Amount: US format (1,234.56) with comma thousands
    - Columns: Día | NúmDoc | Descripción | Oficina | Monto | Saldo
    - Nature: by balance direction (saldo > prev_saldo → crédito)
    - Single amount column (débitos or créditos — only one non-zero per row)
    """

    banco_nombre = "itau"
    invertir_lado = "extracto"

    ITAU_NIT = "890.903.937"

    def puede_parsear(self, texto: str) -> bool:
        texto_lower = texto.lower()
        texto_upper = texto.upper()
        return (
            ("itaú" in texto_lower or "itau" in texto_lower)
            and self.ITAU_NIT in texto
        ) or ("itaú" in texto_lower and "890.903" in texto)

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        year, mes = self._extract_year_and_month(texto)
        movimientos: list[MovimientoExtracto] = []
        prev_balance = self._extract_saldo_anterior(texto)
        seq = 1

        for line in texto.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Movement format: DD NNN Description City Amount Balance
            # Day = 1-2 digits, Doc = digits, Description = text, City = text, Amount = US format, Balance = US format
            # Skip summary lines with leading dots and non-movement text
            for m in re.finditer(
                r"^(\d{1,2})\s+(\d+)\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$",
                line,
            ):
                dia = int(m.group(1))
                desc_raw = m.group(3)
                valor_str = m.group(4)
                balance_str = m.group(5)

                fecha = self._build_date(dia, year, mes)
                if not fecha:
                    continue

                valor = parse_amount(valor_str, formato="us")
                if valor is None:
                    continue

                descripcion = normalize_description(desc_raw)
                naturaleza = self._resolve_naturaleza(balance_str, prev_balance)

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

        logger.info("Itau parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        cuenta = ""
        saldo_anterior = 0.0
        saldo_final = 0.0
        periodo_inicio = date.today()
        periodo_fin = date.today()

        for line in texto.split("\n"):
            # Account: "Cuenta de ahorros No. 501-05951-7"
            m = re.search(r"Cuenta\s+de\s+ahorros\s+No\.?\s*([\d\-]+)", line, re.IGNORECASE)
            if not m:
                m = re.search(r"No\.?\s*([\d\-]+)", line)
            if m:
                cue = normalize_cuenta(m.group(1))
                if len(cue) >= 6:
                    cuenta = cue

            # Period: "01/03/2026 AL 31/03/2026"
            m = re.search(r"(\d{2}/\d{2}/\d{4})\s+AL\s+(\d{2}/\d{2}/\d{4})", line, re.IGNORECASE)
            if m:
                periodo_inicio = self._parse_as_date(m.group(1))
                periodo_fin = self._parse_as_date(m.group(2))

            # Opening balance: "Saldo al 28/02/2026 . . . . 37,851.18"
            m = re.search(r"Saldo\s+al\s+\d{2}/\d{2}/\d{4}[.\s]+([\d,]+\.\d{2})", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_anterior = v

            # Final balance: "Saldo Final . . . . . . . . 3,000,055,756.84"
            m = re.search(r"Saldo\s+Final[.\s]+([\d,]+\.\d{2})", line, re.IGNORECASE)
            if m:
                v = parse_amount(m.group(1), formato="us")
                if v is not None:
                    saldo_final = v

        return InfoExtracto(
            banco="Itaú Colombia",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    def _extract_year_and_month(self, texto: str) -> tuple[int | None, int | None]:
        for line in texto.split("\n"):
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+AL\s+\d{2}/\d{2}/\d{4}", line, re.IGNORECASE)
            if m:
                return int(m.group(3)), int(m.group(2))
        return None, None

    def _extract_saldo_anterior(self, texto: str) -> float | None:
        m = re.search(
            r"Saldo\s+al\s+\d{2}/\d{2}/\d{4}[.\s]+([\d,]+\.\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="us")
            if v is not None:
                return v
        return None

    def _resolve_naturaleza(self, balance_str: str, prev_balance: float | None) -> str:
        if balance_str is not None and prev_balance is not None:
            balance = parse_amount(balance_str, formato="us")
            if balance is not None and balance != prev_balance:
                return "credito" if balance > prev_balance else "debito"
        return "credito"

    def _build_date(self, dia: int, year: int | None, mes: int | None) -> date | None:
        if year is None or mes is None:
            return None
        try:
            return date(year, mes, dia)
        except ValueError:
            return None

    @staticmethod
    def _parse_as_date(ddmmyyyy: str) -> date:
        d, m, y = ddmmyyyy.split("/")
        return date(int(y), int(m), int(d))
