"""Generic regex-based parser for unrecognized bank statements (v2).

Improvements over v1:
- Date detection anywhere in line (not just at start).
- Multi-line description support (continuation lines without a date).
- Header / saldo line filtering.
- Column-layout detection (debit/credit vs amount/balance).
- Smart metadata extraction (bank name, account, period, saldos).
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)

# Universal date patterns (ordered: more specific first)
DATE_PATTERNS = [
    r"\d{1,2}/\d{1,2}/\d{4}",
    r"\d{1,2}-\d{1,2}-\d{4}",
    r"\d{4}-\d{1,2}-\d{1,2}",
    r"\d{1,2}/\d{1,2}",
]

# Amount patterns
AMOUNT_PATTERN_US = r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b"
AMOUNT_PATTERN_CO = r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b"

NATURE_DEBITO_KW = [
    "CARGO", "DEBITO", "RETIRO", "PAGO", "CHEQUE", "COMISION",
    "GMF", "4X1000", "IMPUESTO", "TRANSFERENCIA DE",
]
NATURE_CREDITO_KW = [
    "ABONO", "CREDITO", "CONSIGNACION", "DEPOSITO", "INTERESES",
    "RENDIMIENTO", "TRANSFERENCIA A", "NOTA CREDITO",
]

# Lines that look like headers (all caps, few digits)
HEADER_PATTERN = re.compile(r"^[A-Z\s]{15,}$")

# Lines that look like saldo/totals (contain saldo keywords + amount at end)
SALDO_KW_RE = re.compile(r"\bSALDO\b|\bTOTAL\b|\bSUMA\b", re.IGNORECASE)

# Bank keyword → (display name, banco_nombre)
# Ordered: more specific/unique patterns earlier
_BANK_DETECTION: list[tuple[str, str, str]] = [
    # Keyword pattern, display name, banco_nombre
    ("FONDO DE INVERSION COLECTIVA", "FIC", "fic"),
    ("bancoagrario.gov.co", "Banco Agrario", "banco_agrario"),
    ("BANCO AGRARIO", "Banco Agrario", "banco_agrario"),
    ("BANAGRARIO", "Banco Agrario", "banco_agrario"),
    ("AHORROS ESPECIALES P. JURIDIC", "Davivienda", "davibanck"),
    ("bancodebogota.com", "Banco de Bogota", "bogota"),
    ("BANCO DE BOGOTA", "Banco de Bogota", "bogota"),
    ("BANCO DE OCCIDENTE", "Banco de Occidente", "occidente"),
    ("FIDUOCCIDENTE", "Banco de Occidente", "occidente"),
    ("BANCOLOMBIA", "Bancolombia", "bancolombia"),
    ("DAVIVIENDA", "Davivienda", "davivienda"),
    ("SERFINANZA", "Serfinanza", "serfinanza"),
    ("BANCO GNB", "Banco GNB", "banco_gnb"),
    ("BANCO POPULAR", "Banco Popular", "banco_popular"),
    ("BANCOOMEVA", "Bancoomeva", "bancoomeva"),
    ("AV VILLAS", "AV Villas", "avvillas"),
    ("COLPATRIA", "Scotiabank Colpatria", "colpatria"),
    ("SCOTIABANK", "Scotiabank Colpatria", "colpatria"),
    ("BANCO CAJA SOCIAL", "Banco Caja Social", "banco_caja_social"),
    ("BANCO CAJA SOCIAL ", "Banco Caja Social", "banco_caja_social"),  # trailing space for exact match
    ("BBVA", "BBVA", "bbva"),
    ("ITAU", "Itau", "itau"),
    # NITs
    ("860003020", "BBVA", "bbva"),
    ("860034313", "Davivienda", "davivienda"),
    ("860007335", "Banco Caja Social", "banco_caja_social"),
    ("890.903.937", "Itau", "itau"),
    ("890903937", "Itau", "itau"),
]

# Spanish month names → number
_MES_NOMBRE: dict[str, str] = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
    "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
    "SEPTIEMBRE": "09", "OCTUBRE": "10", "NOVIEMBRE": "11", "DICIEMBRE": "12",
}
_MES_ABR: dict[str, str] = {
    "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AGO": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12",
}

# Period patterns
_PERIOD_RANGES = [
    # DD/MM/YYYY al DD/MM/YYYY or DD-MM-YYYY al DD-MM-YYYY
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+(?:AL?|A)\s+(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
    # YYYY/MM/DD - YYYY/MM/DD
    r"(\d{4}/\d{2}/\d{2})\s*-\s*(\d{4}/\d{2}/\d{2})",
    # DD-mon-YYYY a DD-mon-YYYY
    r"(\d{1,2}-[A-Za-z]{3}-\d{4})\s+a\s+(\d{1,2}-[A-Za-z]{3}-\d{4})",
    # "1 de Marzo a 31 de Marzo de 2026"
    r"(\d{1,2})\s+de\s+(\w+)\s+a\s+(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",
    # "1 AL 31 MAR 2026"
    r"(\d{1,2})\s+AL\s+(\d{1,2})\s+(\w{3})\s+(\d{4})",
]
_PERIOD_SINGLE_MONTH = [
    # Full month name + year: "MARZO 2026"
    r"(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPTIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)\s+(\d{4})",
    # "Mes AAAA" format: "ENERO/2026"
    r"(\w+)/(\d{4})",
]


class GenericParser(BankParser):
    """Generic regex parser for unrecognized bank formats (v2).

    Tries universal patterns: date anywhere + text + amount(s) per line.
    Supports multi-line descriptions and basic column-layout detection.
    """

    banco_nombre = "generico"

    def puede_parsear(self, texto: str) -> bool:
        return True  # Always matches as fallback

    # ------------------------------------------------------------------
    # Main parsing entry point
    # ------------------------------------------------------------------

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        lines = texto.split("\n")

        # Detect column layout from header lines
        layout = self._detect_layout(lines[:30])

        if layout == "debit_credit":
            return self._parse_debit_credit(lines)
        elif layout == "amount_balance":
            return self._parse_amount_balance(lines)
        else:
            return self._parse_standard(lines)

    # ------------------------------------------------------------------
    # Layout detection
    # ------------------------------------------------------------------

    def _detect_layout(self, header_lines: list[str]) -> str:
        """Detect table layout from header lines."""
        header_text = "\n".join(header_lines).upper()
        has_debito = bool(re.search(r"\bDEBITO[S]?\b", header_text))
        has_credito = bool(re.search(r"\bCREDITO[S]?\b", header_text))
        has_saldo = bool(re.search(r"\bSALDO\b", header_text))
        has_valor = bool(re.search(r"\bVALOR\b", header_text))

        if has_debito and has_credito:
            return "debit_credit"
        if has_valor and has_saldo:
            return "amount_balance"
        return "standard"

    # ------------------------------------------------------------------
    # Standard parser (v2 — date anywhere + multi-line)
    # ------------------------------------------------------------------

    def _parse_standard(self, lines: list[str]) -> list[MovimientoExtracto]:
        movimientos: list[MovimientoExtracto] = []
        seq = 1
        pending_date: date | None = None
        pending_remaining = ""

        for line in lines:
            line = line.strip()
            if not line or len(line) < 3:
                continue

            # Skip lines that look like headers or saldo summaries
            if self._is_skip_line(line):
                continue

            fecha, remaining = self._extract_date_anywhere(line)

            if fecha:
                # Save previous pending movement
                if pending_date and pending_remaining:
                    mov = self._build_movement(seq, pending_date, pending_remaining)
                    if mov:
                        movimientos.append(mov)
                        seq += 1

                pending_date = fecha
                pending_remaining = remaining
            elif pending_date:
                # Continuation of previous description
                pending_remaining += " " + line

        # Don't forget the last pending movement
        if pending_date and pending_remaining:
            mov = self._build_movement(seq, pending_date, pending_remaining)
            if mov:
                movimientos.append(mov)

        logger.info("Generic parser (standard): extracted %d movements", len(movimientos))
        return movimientos

    # ------------------------------------------------------------------
    # Debit/Credit column parser
    # ------------------------------------------------------------------

    def _parse_debit_credit(self, lines: list[str]) -> list[MovimientoExtracto]:
        """Parse lines that have separate debit/credit columns."""
        movimientos: list[MovimientoExtracto] = []
        seq = 1

        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if self._is_skip_line(line):
                continue

            fecha, remaining = self._extract_date_anywhere(line)
            if not fecha:
                continue

            amounts_co = re.findall(AMOUNT_PATTERN_CO, remaining)
            amounts_us = re.findall(AMOUNT_PATTERN_US, remaining)

            if len(amounts_co) >= 2:
                a1 = parse_amount(amounts_co[0], formato="co") or 0
                a2 = parse_amount(amounts_co[1], formato="co") or 0
                fmt = "co"
            elif len(amounts_us) >= 2:
                a1 = parse_amount(amounts_us[0], formato="us") or 0
                a2 = parse_amount(amounts_us[1], formato="us") or 0
                fmt = "us"
            else:
                continue

            # Column 1 = debito, column 2 = credito
            if a1 > 0 and a2 == 0:
                valor, naturaleza = a1, "debito"
            elif a2 > 0 and a1 == 0:
                valor, naturaleza = a2, "credito"
            else:
                continue

            desc = self._extract_desc_until_amount(remaining, amounts_co[0] if amounts_co else amounts_us[0])

            movimientos.append(MovimientoExtracto(
                id=f"EXT-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc),
            ))
            seq += 1

        logger.info("Generic parser (debit_credit): extracted %d movements", len(movimientos))
        return movimientos

    # ------------------------------------------------------------------
    # Amount/Balance column parser
    # ------------------------------------------------------------------

    def _parse_amount_balance(self, lines: list[str]) -> list[MovimientoExtracto]:
        """Parse lines that have amount + balance columns (single amount)."""
        movimientos: list[MovimientoExtracto] = []
        seq = 1

        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if self._is_skip_line(line):
                continue

            fecha, remaining = self._extract_date_anywhere(line)
            if not fecha:
                continue

            amounts_co = re.findall(AMOUNT_PATTERN_CO, remaining)
            amounts_us = re.findall(AMOUNT_PATTERN_US, remaining)

            valor = None
            matched = []
            if amounts_co:
                valor = parse_amount(amounts_co[0], formato="co")
                matched = amounts_co
            elif amounts_us:
                valor = parse_amount(amounts_us[0], formato="us")
                matched = amounts_us

            if valor is None or valor == 0:
                continue

            desc = self._extract_desc_until_amount(remaining, matched[0])
            naturaleza = self._detect_naturaleza(desc)

            movimientos.append(MovimientoExtracto(
                id=f"EXT-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc),
            ))
            seq += 1

        logger.info("Generic parser (amount_balance): extracted %d movements", len(movimientos))
        return movimientos

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_date_anywhere(self, line: str) -> tuple[date | None, str]:
        """Find a date anywhere in the line. Returns (date, remaining_text)."""
        for pattern in DATE_PATTERNS:
            m = re.search(rf"({pattern})\b", line)
            if m:
                fecha = parse_date(m.group(1))
                if fecha:
                    # remaining = text before date + text after date
                    remaining = line[:m.start()].strip() + " " + line[m.end():].strip()
                    return fecha, remaining.strip()
        return None, line

    def _extract_desc_until_amount(self, remaining: str, amount_str: str) -> str:
        """Extract description text before the first amount."""
        idx = remaining.find(amount_str)
        if idx != -1:
            return remaining[:idx].strip()
        return remaining.strip()

    def _build_movement(
        self, seq: int, fecha: date, remaining: str
    ) -> MovimientoExtracto | None:
        """Build a single movement from date and remaining text."""
        amounts_co = re.findall(AMOUNT_PATTERN_CO, remaining)
        amounts_us = re.findall(AMOUNT_PATTERN_US, remaining)

        valor = None
        matched = []
        fmt = "us"
        if amounts_co:
            valor = parse_amount(amounts_co[0], formato="co")
            matched = amounts_co
            fmt = "co"
        elif amounts_us:
            valor = parse_amount(amounts_us[0], formato="us")
            matched = amounts_us

        if valor is None or valor == 0:
            return None

        desc = self._extract_desc_until_amount(remaining, matched[0])
        naturaleza = self._detect_naturaleza(desc)

        # Two amounts found → possible debit/credit columns
        if len(matched) >= 2:
            a1 = parse_amount(matched[0], formato=fmt) or 0
            a2 = parse_amount(matched[1], formato=fmt) or 0
            if a1 > 0 and a2 == 0:
                return MovimientoExtracto(
                    id=f"EXT-{seq:04d}",
                    fecha=fecha,
                    valor=a1,
                    naturaleza="debito",
                    descripcion=normalize_description(desc),
                )
            elif a2 > 0 and a1 == 0:
                return MovimientoExtracto(
                    id=f"EXT-{seq:04d}",
                    fecha=fecha,
                    valor=a2,
                    naturaleza="credito",
                    descripcion=normalize_description(desc),
                )

        return MovimientoExtracto(
            id=f"EXT-{seq:04d}",
            fecha=fecha,
            valor=abs(valor),
            naturaleza=naturaleza,
            descripcion=normalize_description(desc),
        )

    def _is_skip_line(self, line: str) -> bool:
        """Return True if line looks like a header or saldo summary."""
        if not re.search(r"\d", line):
            # Pure text — likely a header
            return True
        if SALDO_KW_RE.search(line) and len(re.findall(r"\d", line)) <= 10:
            # Saldo line — typically "SALDO ANTERIOR: 1234.56"
            return True
        return False

    def _detect_naturaleza(self, desc: str) -> str:
        desc_upper = desc.upper()
        for kw in NATURE_CREDITO_KW:
            if kw in desc_upper:
                return "credito"
        for kw in NATURE_DEBITO_KW:
            if kw in desc_upper:
                return "debito"
        return "debito"

    # ------------------------------------------------------------------
    # Metadata extraction (v2 — smart with bank knowledge)
    # ------------------------------------------------------------------

    def extraer_info(self, texto: str) -> InfoExtracto:
        texto_upper = texto.upper()

        # Detect bank
        banco = "Desconocido"
        banco_nombre = "generico"
        for pattern, display, slug in _BANK_DETECTION:
            if pattern.upper() in texto_upper:
                banco = display
                banco_nombre = slug
                break

        # Extract account number (multiple regexes)
        cuenta = ""
        for line in texto.split("\n"):
            line_s = line.strip()
            # Pattern 1: CUENTA / CTA / No. keyword
            m = re.search(r"(?:CUENTA|CTA|No\.?)\s*[:\s]*([\d\-]+)", line_s, re.IGNORECASE)
            if not m:
                # Pattern 2: standalone numeric line (8-16 digits)
                m = re.match(r"^(\d{8,16})\s*$", line_s)
            if m:
                candidate = normalize_cuenta(m.group(1))
                if len(candidate) >= 6:
                    if not cuenta or len(candidate) < len(cuenta):
                        cuenta = candidate

        # Extract period
        periodo_inicio, periodo_fin = self._extract_period_range(texto, texto_upper)

        # Extract saldos
        saldo_anterior, saldo_final = self._extract_saldos(texto)

        return InfoExtracto(
            banco=f"{banco} ({banco_nombre})" if banco != "Desconocido" else banco,
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio or date.today(),
            periodo_fin=periodo_fin or date.today(),
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )

    @staticmethod
    def _extract_period_range(texto: str, texto_upper: str) -> tuple[date | None, date | None]:
        """Try to extract period range from common patterns."""
        # Try date range patterns
        for pattern in _PERIOD_RANGES:
            m = re.search(pattern, texto, re.IGNORECASE)
            if m:
                g1, g2 = m.group(1), m.group(2)
                # Handle "1 de Marzo a 31 de Marzo de 2026" (5 groups)
                if len(m.groups()) >= 5:
                    dia_ini, mes_nom_ini, dia_fin, mes_nom_fin, year = m.group(1, 2, 3, 4, 5)
                    try:
                        mi = list(_MES_NOMBRE.values())[list(_MES_NOMBRE.keys()).index(mes_nom_ini.upper())] if mes_nom_ini.upper() in _MES_NOMBRE else None
                        mf = list(_MES_NOMBRE.values())[list(_MES_NOMBRE.keys()).index(mes_nom_fin.upper())] if mes_nom_fin.upper() in _MES_NOMBRE else None
                        if mi and mf:
                            mi_num = int(mi); mf_num = int(mf)
                            return date(int(year), mi_num, int(dia_ini)), date(int(year), mf_num, int(dia_fin))
                    except (ValueError, IndexError):
                        pass
                # Handle "1 AL 31 MAR 2026" (4 groups)
                elif len(m.groups()) >= 4 and m.lastindex and m.lastindex >= 4:
                    dia_ini, dia_fin, mes_abr, year = m.group(1, 2, 3, 4)
                    mes_num = _MES_ABR.get(mes_abr.upper())
                    if mes_num:
                        try:
                            return date(int(year), int(mes_num), int(dia_ini)), date(int(year), int(mes_num), int(dia_fin))
                        except ValueError:
                            pass
                else:
                    d1 = parse_date(g1)
                    d2 = parse_date(g2)
                    if d1 and d2:
                        return d1, d2

        # Try single month patterns
        for pattern in _PERIOD_SINGLE_MONTH:
            m = re.search(pattern, texto, re.IGNORECASE)
            if m:
                try:
                    if len(m.groups()) == 2:
                        mes_str, year_str = m.group(1), m.group(2)
                    else:
                        continue
                    mes_num = _MES_NOMBRE.get(mes_str.upper()) or _MES_ABR.get(mes_str.upper())
                    if mes_num:
                        year = int(year_str)
                        _, last_day = calendar.monthrange(year, int(mes_num))
                        return date(year, int(mes_num), 1), date(year, int(mes_num), last_day)
                except ValueError:
                    pass

        return None, None

    @staticmethod
    def _extract_saldos(texto: str) -> tuple[float, float]:
        """Extract saldo anterior and saldo final from common patterns."""
        saldo_anterior = 0.0
        saldo_final = 0.0

        # Pattern: SALDO ANTERIOR X,XX SALDO ACTUAL/FINAL Y,YY
        m = re.search(
            r"SALDO\s+ANTERIOR\s+((?:\d{1,3}(?:[.,]\d{3})*)?[,.]?\d{2})\s+SALDO\s+(?:ACTUAL|FINAL)\s+((?:\d{1,3}(?:[.,]\d{3})*)?[,.]?\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1))
            if v is not None:
                saldo_anterior = v
            v = parse_amount(m.group(2))
            if v is not None:
                saldo_final = v
            return saldo_anterior, saldo_final

        # Pattern: Saldo Inicial DD MMM AAAA AMOUNT
        m = re.search(
            r"Saldo\s+Inicial\s+\d{1,2}\s+[A-Z]{3}\s+\d{4}\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="co")
            if v is not None:
                saldo_anterior = v

        # Pattern: Saldo Final DD MMM AAAA AMOUNT
        m = re.search(
            r"Saldo\s+Final\s+\d{1,2}\s+[A-Z]{3}\s+\d{4}\s+((?:\d{1,3}(?:\.\d{3})*)?,?\d{2})",
            texto, re.IGNORECASE,
        )
        if m:
            v = parse_amount(m.group(1), formato="co")
            if v is not None:
                saldo_final = v

        # Generic: "Saldo Anterior: $1,234.56" or "Saldo Inicial: $1,234.56"
        for saldo_type, target in [("ANTERIOR", "sa"), ("INICIAL", "sa"), ("FINAL", "sf"), ("ACTUAL", "sf")]:
            m = re.search(
                rf"Saldo\s+{saldo_type}\s*:?\s*\$?((?:\d{1,3}(?:[.,]\d{3})*)?[,.]?\d{{2}})",
                texto, re.IGNORECASE,
            )
            if m:
                v = parse_amount(m.group(1))
                if v is not None:
                    if target == "sa" and saldo_anterior == 0:
                        saldo_anterior = v
                    elif target == "sf" and saldo_final == 0:
                        saldo_final = v

        return saldo_anterior, saldo_final
