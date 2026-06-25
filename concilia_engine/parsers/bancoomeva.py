"""Parser for Bancoomeva bank statements (regex only, no LLM)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date
from concilia_engine.parsers.base import BankParser

logger = logging.getLogger(__name__)


class BancoomevaParser(BankParser):
    """Parser for Bancoomeva statements.

    Particularities:
    - Date: DD/MM/YYYY (complete, on each movement line)
    - Amount: US format (1,234,567.89), prefixed with ``$``
    - Layout: separate DEBITO and CREDITO columns (``$ 0.00 $ 3,273.53 $ 83,939,922.59``)
    - Nature: determined by which column has non-zero value
    - Multi-line descriptions: office may span 2 lines (e.g. "CARTAGENA DE" + "INDIAS")
    """

    banco_nombre = "bancoomeva"
    invertir_lado = "extracto"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        return "BANCOOMEVA" in texto_upper or "890480184" in texto

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")
        seq = 1
        pending_description = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue

            m = re.search(
                r"(\d{2}/\d{2}/\d{4})\s+(.+?)\s+\$\s+([\d,]+\.\d{2})\s+\$\s+([\d,]+\.\d{2})\s+\$\s+([\d,]+\.\d{2})\s*$",
                line,
            )
            if not m:
                # Could be a continuation line (city/office wrapped)
                if line and len(line) < 40 and not re.search(r"\d", line):
                    pending_description = line
                continue

            fecha = parse_date(m.group(1))
            if fecha is None:
                continue

            desc_raw = m.group(2).strip()
            if pending_description:
                desc_raw = f"{desc_raw} {pending_description}"
                pending_description = ""

            debito_val = parse_amount(m.group(3), formato="us") or 0.0
            credito_val = parse_amount(m.group(4), formato="us") or 0.0

            if debito_val == 0 and credito_val == 0:
                continue

            if debito_val > 0 and credito_val > 0:
                valor = max(debito_val, credito_val)
                naturaleza = "debito" if debito_val >= credito_val else "credito"
            elif debito_val > 0:
                valor = debito_val
                naturaleza = "debito"
            else:
                valor = credito_val
                naturaleza = "credito"

            movimientos.append(MovimientoExtracto(
                id=f"OME-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=normalize_description(desc_raw),
            ))
            seq += 1

        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        texto_full = texto

        cuenta = ""
        m = re.search(r"CUENTA\s+No\s*:\s*(\d+)", texto)
        if m:
            cuenta = normalize_cuenta(m.group(1))

        periodo_inicio = None
        periodo_fin = None
        m = re.search(r"DEL\s*:\s*(\d{2}/\d{2}/\d{4})\s+AL\s*:\s*(\d{2}/\d{2}/\d{4})", texto)
        if m:
            periodo_inicio = parse_date(m.group(1))
            periodo_fin = parse_date(m.group(2))

        # Saldos: inferred from first and last movement's running balance.
        # Each line: DD/MM/YYYY DESC $ DEBITO $ CREDITO $ SALDO
        saldo_anterior = 0.0
        saldo_final = 0.0
        mov_matches = re.findall(
            r"(\d{2}/\d{2}/\d{4})\s+(.+?)\s+\$\s+([\d,]+\.\d{2})\s+\$\s+([\d,]+\.\d{2})\s+\$\s+([\d,]+\.\d{2})",
            texto_full,
        )
        if mov_matches:
            # First movement: saldo_anterior = saldo - credito + debito
            _, _, d1, c1, s1 = mov_matches[0]
            s1_val = parse_amount(s1, formato="us") or 0.0
            d1_val = parse_amount(d1, formato="us") or 0.0
            c1_val = parse_amount(c1, formato="us") or 0.0
            saldo_anterior = s1_val - c1_val + d1_val
            # Last movement: saldo_final = its running balance
            saldo_final = parse_amount(mov_matches[-1][4], formato="us") or 0.0

        return InfoExtracto(
            banco="Bancoomeva",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio or date.today(),
            periodo_fin=periodo_fin or date.today(),
            saldo_anterior=saldo_anterior,
            saldo_final=saldo_final,
        )


def _extract_amount(texto: str, pattern: str) -> float:
    m = re.search(pattern, texto)
    if m:
        return parse_amount(m.group(1), formato="us") or 0.0
    return 0.0
