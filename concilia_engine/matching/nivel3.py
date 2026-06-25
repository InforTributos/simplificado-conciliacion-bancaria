"""Nivel 3: N:M group matching — tridirectional subset-sum matching."""

from __future__ import annotations

import logging
from datetime import date

from concilia_engine.config import MatchConfig
from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto
from concilia_engine.normalizer import similarity

logger = logging.getLogger(__name__)


def match_grupo(
    extracto: list[MovimientoExtracto],
    contabilidad: list[MovimientoContable],
    config: MatchConfig,
) -> tuple[list[Match], list[MovimientoExtracto], list[MovimientoContable]]:
    """Execute Level 3 N:M group matching.

    Direction A: 1 extracto <-> N contabilidad
    Direction B: N extracto <-> 1 contabilidad
    Direction C: N extracto <-> M contabilidad (description-grouped)
    """
    matches: list[Match] = []
    matched_ext_ids: set[str] = set()
    matched_ctb_ids: set[str] = set()

    # Direction A: 1 extracto <-> N contabilidad
    for ext in extracto:
        if ext.id in matched_ext_ids:
            continue

        same_nature_ctb = [
            c for c in contabilidad
            if c.id not in matched_ctb_ids
            and c.naturaleza_matching == ext.naturaleza_matching
            and abs((c.fecha - ext.fecha).days) <= config.max_dias_diferencia
        ]

        subsets = _find_subset_sum(
            same_nature_ctb,
            ext.valor,
            config.tolerancia_monto,
            config.max_grupo_items,
        )

        if subsets:
            best = min(subsets, key=len)  # Prefer fewer elements
            confianza = _calc_confidence_1n(best, ext.descripcion)

            matches.append(Match(
                nivel=3,
                confianza=round(confianza, 2),
                movimiento_extracto=ext,
                movimiento_contabilidad=best,
                tipo="extracto_uno_contabilidad_muchos",
            ))
            matched_ext_ids.add(ext.id)
            for c in best:
                matched_ctb_ids.add(c.id)

    # Direction B: N extracto <-> 1 contabilidad
    for ctb in contabilidad:
        if ctb.id in matched_ctb_ids:
            continue

        same_nature_ext = [
            e for e in extracto
            if e.id not in matched_ext_ids
            and e.naturaleza_matching == ctb.naturaleza_matching
            and abs((e.fecha - ctb.fecha).days) <= config.max_dias_diferencia
        ]

        subsets = _find_subset_sum(
            same_nature_ext,
            ctb.valor,
            config.tolerancia_monto,
            config.max_grupo_items,
        )

        if subsets:
            best = min(subsets, key=len)
            confianza = _calc_confidence_1n(best, ctb.descripcion)

            matches.append(Match(
                nivel=3,
                confianza=round(confianza, 2),
                movimiento_extracto=best,
                movimiento_contabilidad=ctb,
                tipo="extracto_muchos_contabilidad_uno",
            ))
            for e in best:
                matched_ext_ids.add(e.id)
            matched_ctb_ids.add(ctb.id)

    # Direction C: N extracto <-> M contabilidad
    remaining_ext_c = [e for e in extracto if e.id not in matched_ext_ids]
    remaining_ctb_c = [c for c in contabilidad if c.id not in matched_ctb_ids]

    nm_matches = _match_nm_by_description(
        remaining_ext_c, remaining_ctb_c, config
    )
    for m in nm_matches:
        matches.append(m)
        if isinstance(m.movimiento_extracto, list):
            for e in m.movimiento_extracto:
                matched_ext_ids.add(e.id)
        else:
            matched_ext_ids.add(m.movimiento_extracto.id)
        if isinstance(m.movimiento_contabilidad, list):
            for c in m.movimiento_contabilidad:
                matched_ctb_ids.add(c.id)
        else:
            matched_ctb_ids.add(m.movimiento_contabilidad.id)

    remaining_ext = [e for e in extracto if e.id not in matched_ext_ids]
    remaining_ctb = [c for c in contabilidad if c.id not in matched_ctb_ids]

    return matches, remaining_ext, remaining_ctb


def _find_subset_sum(
    items: list,
    target: float,
    tolerance: float,
    max_items: int,
) -> list[list]:
    """Find subsets of items whose values sum to target within tolerance.

    Uses backtracking with pruning. Returns all valid subsets.
    """
    if not items or max_items < 1:
        return []

    # Sort descending for better pruning
    sorted_items = sorted(items, key=lambda x: x.valor, reverse=True)
    results: list[list] = []

    def backtrack(start: int, current_sum: float, current: list) -> None:
        if abs(current_sum - target) <= tolerance and len(current) >= 1:
            results.append(list(current))
            if len(results) >= 10:  # Limit to prevent combinatorial explosion
                return
            return

        if current_sum > target + tolerance:
            return
        if len(current) >= max_items:
            return
        if len(results) >= 10:
            return

        for i in range(start, len(sorted_items)):
            current.append(sorted_items[i])
            backtrack(i + 1, current_sum + sorted_items[i].valor, current)
            current.pop()

    backtrack(0, 0.0, [])
    return results


def _calc_confidence_1n(group: list, reference_desc: str) -> float:
    """Calculate confidence for 1:N match."""
    base = 0.70
    # Description similarity bonus
    if group:
        avg_sim = sum(similarity(m.descripcion, reference_desc) for m in group) / len(group)
        if avg_sim >= 0.3:
            base += 0.10
    # Size penalty
    penalty = max(0, (len(group) - 3)) * 0.02
    return max(0.10, base - penalty)


def _match_nm_by_description(
    extracto: list[MovimientoExtracto],
    contabilidad: list[MovimientoContable],
    config: MatchConfig,
) -> list[Match]:
    """Direction C: Group by similar description, then match sums."""
    matches: list[Match] = []
    used_ext: set[str] = set()
    used_ctb: set[str] = set()

    # Group extracto by description similarity
    ext_groups = _group_by_description(extracto)
    ctb_groups = _group_by_description(contabilidad)

    for ext_key, ext_group in ext_groups.items():
        if any(e.id in used_ext for e in ext_group):
            continue

        ext_sum = sum(e.valor for e in ext_group)
        ext_nature = ext_group[0].naturaleza_matching if ext_group else None

        for ctb_key, ctb_group in ctb_groups.items():
            if any(c.id in used_ctb for c in ctb_group):
                continue

            ctb_nature = ctb_group[0].naturaleza_matching if ctb_group else None
            if ext_nature != ctb_nature:
                continue

            ctb_sum = sum(c.valor for c in ctb_group)
            if abs(ext_sum - ctb_sum) > config.tolerancia_monto:
                continue

            # Check date window
            if not _within_date_window(ext_group, ctb_group, config.max_dias_diferencia):
                continue

            # Check size limit
            if len(ext_group) + len(ctb_group) > config.max_grupo_items * 2:
                continue

            # Calculate confidence
            confianza = 0.60
            desc_sim = similarity(ext_key, ctb_key)
            if desc_sim >= 0.3:
                confianza += 0.10
            penalty = max(0, (max(len(ext_group), len(ctb_group)) - 3)) * 0.02
            confianza = max(0.10, confianza - penalty)

            matches.append(Match(
                nivel=3,
                confianza=round(confianza, 2),
                movimiento_extracto=ext_group,
                movimiento_contabilidad=ctb_group,
                tipo="extracto_muchos_contabilidad_muchos",
            ))

            for e in ext_group:
                used_ext.add(e.id)
            for c in ctb_group:
                used_ctb.add(c.id)
            break  # Move to next ext_group

    return matches


def _group_by_description(items: list) -> dict[str, list]:
    """Group items by similar description (first 3 significant words)."""
    groups: dict[str, list] = {}
    for item in items:
        key = " ".join(item.descripcion.split()[:3]) if item.descripcion else "SIN_DESC"
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
    return groups


def _within_date_window(
    group_a: list,
    group_b: list,
    max_dias: int,
) -> bool:
    """Check if all items in both groups are within the date window."""
    if not group_a or not group_b:
        return False

    dates_a = [item.fecha for item in group_a]
    dates_b = [item.fecha for item in group_b]

    centroid_a = _date_centroid(dates_a)
    centroid_b = _date_centroid(dates_b)

    # Check each item against the centroid of the other group
    for item in group_a:
        if abs((item.fecha - centroid_b).days) > max_dias:
            return False
    for item in group_b:
        if abs((item.fecha - centroid_a).days) > max_dias:
            return False

    return True


def _date_centroid(dates: list[date]) -> date:
    """Calculate the centroid (average) date of a list of dates."""
    if not dates:
        return date.today()
    ordinals = [d.toordinal() for d in dates]
    avg = sum(ordinals) // len(ordinals)
    return date.fromordinal(avg)
