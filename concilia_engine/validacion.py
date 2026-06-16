"""Cross-validation between user-provided data and PDF-extracted metadata.

Pure functions (no HTTP concerns) that return None on success or an error
message string on mismatch.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from concilia_engine.models import InfoExtracto


def validar_periodo_contra_pdf(periodo: str | None, info: InfoExtracto) -> str | None:
    """Check that user-provided AAAAMM period overlaps with the PDF's date range.

    Returns None if OK, or an error message string on mismatch.
    Skips validation when:
      - periodo is None (auto-detected)
      - PDF period equals date.today() (parser fallback)
    """
    if not periodo:
        return None

    p_ini, p_fin = info.periodo_inicio, info.periodo_fin

    # Skip if parser fell back to today() (couldn't extract real dates)
    today = date.today()
    if p_ini == today and p_fin == today:
        return None

    year = int(periodo[:4])
    month = int(periodo[4:])
    user_start = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    user_end = date(year, month, last_day)

    if user_end < p_ini or user_start > p_fin:
        return (
            f"El periodo enviado ({periodo}) no coincide con el periodo del extracto "
            f"({p_ini:%d/%m/%Y} - {p_fin:%d/%m/%Y})"
        )
    return None


def validar_cuenta_contra_pdf(cuenta_enviada: str, info: InfoExtracto) -> str | None:
    """Check that user-provided account number matches the PDF's extracted account.

    Returns None if OK, or an error message string on mismatch.
    Matching is substring-based after removing all non-digit characters.
    Skips validation when:
      - cuenta_enviada is empty
      - PDF's numero_cuenta is empty (parser didn't extract it)
    """
    if not cuenta_enviada:
        return None

    cuenta_pdf = info.numero_cuenta
    if not cuenta_pdf:
        return None

    norm_env = re.sub(r"\D", "", cuenta_enviada)
    norm_pdf = re.sub(r"\D", "", cuenta_pdf)

    # Banco Caja Social: only validate last 4 digits (account is partially masked)
    is_bcs = info.banco and "caja social" in info.banco.lower()
    if is_bcs:
        norm_env = norm_env[-4:] if len(norm_env) >= 4 else norm_env
        norm_pdf = norm_pdf[-4:] if len(norm_pdf) >= 4 else norm_pdf

    if norm_env not in norm_pdf and norm_pdf not in norm_env:
        return (
            f"La cuenta bancaria enviada ({cuenta_enviada}) no coincide "
            f"con la cuenta del extracto ({cuenta_pdf})"
        )
    return None
