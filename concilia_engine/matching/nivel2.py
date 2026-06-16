"""Nivel 2: Flexible date match — same value, same nature, date within window."""

from __future__ import annotations

from concilia_engine.config import MatchConfig
from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto
from concilia_engine.normalizer import similarity


def match_fecha_flexible(
    extracto: list[MovimientoExtracto],
    contabilidad: list[MovimientoContable],
    config: MatchConfig,
) -> tuple[list[Match], list[MovimientoExtracto], list[MovimientoContable]]:
    """Execute Level 2 flexible date matching.

    Confidence: 0.90 - (0.05 × days_difference)
    Prioritize minimum day difference. On tie, use description similarity.
    """
    matches: list[Match] = []
    matched_ext_ids: set[str] = set()
    matched_ctb_ids: set[str] = set()

    for ext in extracto:
        if ext.id in matched_ext_ids:
            continue

        candidates = []
        for ctb in contabilidad:
            if ctb.id in matched_ctb_ids:
                continue
            if abs(ctb.valor - ext.valor) > config.tolerancia_monto:
                continue
            if ctb.naturaleza_matching != ext.naturaleza:
                continue

            dias = abs((ctb.fecha - ext.fecha).days)
            if dias == 0 or dias > config.max_dias_diferencia:
                continue  # dias==0 would have been caught in Level 1

            candidates.append((ctb, dias))

        if not candidates:
            continue

        # Sort by days difference, then by description similarity
        candidates.sort(key=lambda x: (x[1], -similarity(x[0].descripcion, ext.descripcion)))
        best_ctb, best_dias = candidates[0]

        confianza = max(0.10, 0.90 - (0.05 * best_dias))

        matches.append(Match(
            nivel=2,
            confianza=round(confianza, 2),
            movimiento_extracto=ext,
            movimiento_contabilidad=best_ctb,
            tipo="fecha_flexible",
            dias_diferencia=best_dias,
        ))
        matched_ext_ids.add(ext.id)
        matched_ctb_ids.add(best_ctb.id)

    remaining_ext = [e for e in extracto if e.id not in matched_ext_ids]
    remaining_ctb = [c for c in contabilidad if c.id not in matched_ctb_ids]

    return matches, remaining_ext, remaining_ctb
