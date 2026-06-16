"""Parser for Fondo de Inversion Colectiva (FIC) statements (regex only, no LLM)."""

from __future__ import annotations

import logging
import re
from datetime import date

from concilia_engine.models import InfoExtracto, MovimientoExtracto
from concilia_engine.normalizer import normalize_cuenta, normalize_description, parse_amount, parse_date

logger = logging.getLogger(__name__)

FIC_MOVEMENT_RE = re.compile(
    r"(\d{2}-[a-z]{3}-\d{4})\s+\d+\s+"
    r"(?:RETIRO\s+POR\s+TRASLADO\s+INTERNO|ADICION|RETIRO)\s+"
    r"(.+?)\s+\$\s*"
    r"([\d.]+,\d{2})"
    r"(?:\s+[\d.]+,\d+\s+\d+)?"
    r"\s*$",
    re.IGNORECASE,
)

ADICION_KW = "ADICION"
RETIRO_KW = "RETIRO"


class FICParser:
    """Parser for Fondo de Inversion Colectiva (FIC) reports.

    Extracts transactions (ADICION/RETIRO) to/from individual banks.
    Not a bank extract per se, but contains valuable movement data.

    Format:
        02-mar-2026 105 ADICION OCCIDENTE AHORROS $ 580.000.000,00 23365,766691 731
        06-mar-2026 414 RETIRO POR TRASLADO INTERNO $ 458.013.133,00 18439,148356 731
    """

    banco_nombre = "fic"

    def puede_parsear(self, texto: str) -> bool:
        texto_upper = texto.upper()
        if "FONDO DE INVERSI" not in texto_upper:
            return False
        return bool(re.search(
            r"\d{2}-[a-z]{3}-\d{4}\s+\d+\s+(?:RETIRO\s+POR\s+TRASLADO\s+INTERNO|ADICION|RETIRO)\s",
            texto,
            re.IGNORECASE,
        ))

    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        movimientos = []
        lines = texto.split("\n")
        seq = 1

        for line in lines:
            line = line.strip()
            if not line:
                continue

            m = FIC_MOVEMENT_RE.search(line)
            if not m:
                continue

            fecha_str, desc_raw, monto_str = m.group(1), m.group(2), m.group(3)

            fecha = parse_date(fecha_str)
            if fecha is None:
                continue

            valor = parse_amount(monto_str, formato="auto")
            if valor is None or valor == 0:
                continue

            naturaleza = "debito" if RETIRO_KW.upper() in line.upper() else "credito"

            descripcion = normalize_description(desc_raw) if desc_raw.strip() else (
                "RETIRO POR TRASLADO INTERNO"
                if "TRASLADO INTERNO" in line.upper()
                else "FIC MOVIMIENTO"
            )

            movimientos.append(MovimientoExtracto(
                id=f"FIC-{seq:04d}",
                fecha=fecha,
                valor=abs(valor),
                naturaleza=naturaleza,
                descripcion=descripcion,
            ))
            seq += 1

        logger.info("FIC parser: extracted %d movements", len(movimientos))
        return movimientos

    def extraer_info(self, texto: str) -> InfoExtracto:
        cuenta = ""
        nombre = ""
        periodo_inicio: date | None = None
        periodo_fin: date | None = None

        for line in texto.split("\n"):
            line = line.strip()

            m = re.search(r"Inversi[oó]n:\s*(\d+)", line, re.IGNORECASE)
            if m:
                cuenta = normalize_cuenta(m.group(1))

            m = re.search(r"Nombre:\s*(.+)", line, re.IGNORECASE)
            if m and not nombre:
                nombre = m.group(1).strip()

            m = re.search(
                r"Periodo:\s*(\d{2}-[a-z]{3}-\d{4})\s+a\s+(\d{2}-[a-z]{3}-\d{4})",
                line,
                re.IGNORECASE,
            )
            if m:
                periodo_inicio = parse_date(m.group(1))
                periodo_fin = parse_date(m.group(2))

        return InfoExtracto(
            banco="Fondo de Inversion Colectiva",
            numero_cuenta=cuenta,
            periodo_inicio=periodo_inicio or date.today(),
            periodo_fin=periodo_fin or date.today(),
            saldo_anterior=0.0,
            saldo_final=0.0,
        )
