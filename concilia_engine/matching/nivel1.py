"""Nivel 1: Exact match — same date, same value, same nature (post-inversion)."""

from __future__ import annotations

from concilia_engine.config import MatchConfig
from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto
from concilia_engine.normalizer import similarity


def match_exacto(
    extracto: list[MovimientoExtracto],
    contabilidad: list[MovimientoContable],
    config: MatchConfig,
) -> tuple[list[Match], list[MovimientoExtracto], list[MovimientoContable]]:
    """Execute Level 1 exact matching.

    Phase 1: Reference-based matching (confidence 1.00)
    Phase 2: Date+Value+Nature matching (confidence 0.95)
    """
    matches: list[Match] = []
    matched_ext_ids: set[str] = set()
    matched_ctb_ids: set[str] = set()

    # Phase 1: Reference-based matching
    for ext in extracto:
        if ext.id in matched_ext_ids or not ext.referencia:
            continue

        candidates = [
            ctb for ctb in contabilidad
            if ctb.id not in matched_ctb_ids
            and ctb.referencia
            and ctb.referencia == ext.referencia
            and abs(ctb.valor - ext.valor) <= config.tolerancia_monto
            and ctb.naturaleza_matching == ext.naturaleza
        ]

        if len(candidates) == 1:
            matches.append(Match(
                nivel=1,
                confianza=1.00,
                movimiento_extracto=ext,
                movimiento_contabilidad=candidates[0],
                tipo="exacto_referencia",
            ))
            matched_ext_ids.add(ext.id)
            matched_ctb_ids.add(candidates[0].id)

    # Phase 2: Date + Value + Nature matching
    for ext in extracto:
        if ext.id in matched_ext_ids:
            continue

        candidates = [
            ctb for ctb in contabilidad
            if ctb.id not in matched_ctb_ids
            and ctb.fecha == ext.fecha
            and abs(ctb.valor - ext.valor) <= config.tolerancia_monto
            and ctb.naturaleza_matching == ext.naturaleza
        ]

        if len(candidates) == 1:
            matches.append(Match(
                nivel=1,
                confianza=0.95,
                movimiento_extracto=ext,
                movimiento_contabilidad=candidates[0],
                tipo="exacto",
            ))
            matched_ext_ids.add(ext.id)
            matched_ctb_ids.add(candidates[0].id)
        elif len(candidates) > 1:
            # Disambiguate by description similarity
            best = max(candidates, key=lambda c: similarity(c.descripcion, ext.descripcion))
            matches.append(Match(
                nivel=1,
                confianza=0.95,
                movimiento_extracto=ext,
                movimiento_contabilidad=best,
                tipo="exacto",
                multiple_candidates=True,
            ))
            matched_ext_ids.add(ext.id)
            matched_ctb_ids.add(best.id)

    # Build remaining lists
    remaining_ext = [e for e in extracto if e.id not in matched_ext_ids]
    remaining_ctb = [c for c in contabilidad if c.id not in matched_ctb_ids]

    return matches, remaining_ext, remaining_ctb
