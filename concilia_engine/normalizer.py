"""Pure normalization functions for dates, amounts, descriptions, and accounts."""

from __future__ import annotations

import re
from datetime import date


def parse_date(
    text: str,
    hint_year: int | None = None,
    hint_month: int | None = None,
) -> date | None:
    """Parse date from multiple formats. Returns None if unparseable."""
    text = text.strip()
    if not text:
        return None

    # DD-mmm-YYYY (e.g., 02-mar-2026) — Spanish month abbreviations
    m = re.match(r"(\d{1,2})-([a-z]{3})-(\d{4})", text, re.IGNORECASE)
    if m:
        month = _spanish_month_abbr(m.group(2).lower())
        if month:
            return _safe_date(int(m.group(3)), month, int(m.group(1)))

    # DD-MM-YYYY or DD/MM/YYYY
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _safe_date(year, month, day)

    # YYYY-MM-DD or YYYY/MM/DD (ISO and AV Villas YYYY/MM/DD)
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _safe_date(year, month, day)

    # DD/MM (no year) — need hint_year
    m = re.match(r"(\d{1,2})[/-](\d{1,2})$", text)
    if m and hint_year:
        day, month = int(m.group(1)), int(m.group(2))
        return _safe_date(hint_year, month, day)

    # DD-MM-YY (2-digit year)
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2})$", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = 2000 + int(m.group(3))
        return _safe_date(year, month, day)

    # Day only (e.g., "03", "16") — need hint_year and hint_month
    m = re.match(r"(\d{1,2})$", text)
    if m and hint_year and hint_month:
        day = int(m.group(1))
        return _safe_date(hint_year, hint_month, day)

    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    """Create a date object, returning None for invalid dates."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


_SPANISH_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _spanish_month_abbr(month_str: str) -> int | None:
    """Map Spanish 3-letter month abbreviation to month number."""
    return _SPANISH_MONTHS.get(month_str.lower())


def parse_amount(text: str, formato: str = "auto") -> float | None:
    """Parse monetary amount from US or Colombian format.

    US format: 1,234,567.89 (comma=thousands, dot=decimal)
    CO format: 1.234.567,89 (dot=thousands, comma=decimal)
    """
    if not text:
        return None

    text = text.strip()
    # Remove currency symbols and whitespace
    text = re.sub(r"[$ \t\xa0]", "", text)

    # Handle parentheses as negative: (1,234.56) -> -1234.56
    negative = False
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
        negative = True
    elif text.startswith("-"):
        text = text[1:]
        negative = True

    if not text:
        return None

    if formato == "auto":
        formato = _detect_number_format(text)

    if formato == "us":
        # Remove thousands separators (commas), keep decimal dot
        text = text.replace(",", "")
    elif formato == "co":
        # Remove thousands separators (dots), convert decimal comma to dot
        text = text.replace(".", "").replace(",", ".")

    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None


def _detect_number_format(text: str) -> str:
    """Detect if number uses US (1,234.89) or CO (1.234,89) format."""
    # Find last separator
    last_dot = text.rfind(".")
    last_comma = text.rfind(",")

    if last_dot == -1 and last_comma == -1:
        return "us"  # No separator, doesn't matter

    if last_dot > last_comma:
        # Dot is last separator -> US format (dot = decimal)
        return "us"
    elif last_comma > last_dot:
        # Comma is last separator -> CO format (comma = decimal)
        return "co"

    return "us"  # Default


def normalize_description(text: str) -> str:
    """Normalize description: trim, collapse spaces, uppercase."""
    if not text:
        return ""
    text = text.strip().upper()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_cuenta(cuenta: str) -> str:
    """Normalize account number: remove dashes, spaces, common prefixes."""
    if not cuenta:
        return ""
    # Remove dashes, spaces, dots
    cuenta = re.sub(r"[-.\s]", "", cuenta)
    return cuenta


def extract_cuenta_corta(cuenta_larga: str, banco: str = "") -> str:
    """Extract short account number from long bank format.

    BBVA: '001309380200554490' -> last 9 digits '938554490' (skip product/office prefix).
    Other banks: return normalized account as-is.
    """
    cuenta = normalize_cuenta(cuenta_larga)
    if len(cuenta) > 12 and banco.lower() == "bbva":
        return cuenta[6:].lstrip("0") or cuenta
    return cuenta


def similarity(a: str, b: str) -> float:
    """Token-based Jaccard similarity between two descriptions."""
    a_norm = normalize_description(a)
    b_norm = normalize_description(b)

    tokens_a = {t for t in a_norm.split() if len(t) >= 2}
    tokens_b = {t for t in b_norm.split() if len(t) >= 2}

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def cuenta_match(cuenta_extracto: str, cuenta_esperada: str) -> bool:
    """Partial/contains matching for account numbers (BR-01.3)."""
    if not cuenta_extracto or not cuenta_esperada:
        return True  # No validation if either is missing

    ext = normalize_cuenta(cuenta_extracto)
    esp = normalize_cuenta(cuenta_esperada)

    # Direct match
    if ext == esp:
        return True

    # Contains match (BBVA long contains short)
    if esp in ext or ext in esp:
        return True

    # Short extraction match
    ext_short = extract_cuenta_corta(ext)
    esp_short = extract_cuenta_corta(esp)
    if ext_short == esp_short:
        return True

    return False
