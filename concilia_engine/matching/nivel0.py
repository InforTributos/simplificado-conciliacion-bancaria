"""Nivel 0: Nature inversion for accounting movements."""

from __future__ import annotations

import copy

from concilia_engine.models import MovimientoContable


def invertir_naturaleza(
    movimientos: list[MovimientoContable],
    invertir: bool = True,
) -> list[MovimientoContable]:
    """Invert accounting nature for matching.

    In double-entry accounting for asset accounts (PUC class 1),
    a debit in the books corresponds to a credit in the bank statement.
    """
    result = []
    for mov in movimientos:
        m = copy.copy(mov)
        if invertir:
            m.naturaleza_matching = "credito" if mov.naturaleza == "debito" else "debito"
        else:
            m.naturaleza_matching = mov.naturaleza
        result.append(m)
    return result
