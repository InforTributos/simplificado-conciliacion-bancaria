"""Main matching engine — orchestrates all 5 levels sequentially."""

from __future__ import annotations

import copy
import logging
import time

from concilia_engine.config import MatchConfig
from concilia_engine.matching.nivel0 import invertir_naturaleza, invertir_naturaleza_extracto
from concilia_engine.matching.nivel1 import match_exacto
from concilia_engine.matching.nivel2 import match_fecha_flexible
from concilia_engine.matching.nivel3 import match_grupo
from concilia_engine.matching.nivel4 import clasificar_no_conciliados
from concilia_engine.models import (
    ConciliacionResult,
    InfoExtracto,
    MovimientoContable,
    MovimientoExtracto,
    ResumenConciliacion,
)

logger = logging.getLogger(__name__)


class MatchingEngine:
    """Execute the 5-level reconciliation algorithm.

    Level 0: Nature inversion (preparation for double-entry matching)
    Level 1: Exact match — date + value + same nature
    Level 2: Flexible date match — value + nature + date within window (N days)
    Level 3: N:M group match — subset-sum backtracking, tridirectional
    Level 4: Classify unmatched + compute cuadre formula
    """

    def conciliar(
        self,
        extracto: list[MovimientoExtracto],
        contabilidad: list[MovimientoContable],
        config: MatchConfig,
        info_extracto: InfoExtracto | None = None,
        saldo_libros: float | None = None,
    ) -> ConciliacionResult:
        """Execute full reconciliation pipeline."""
        start_time = time.time()

        # Level 0: Nature inversion — per-bank (via info_extracto.invertir_lado)
        invertir_lado = "contabilidad"
        if info_extracto is not None:
            invertir_lado = getattr(info_extracto, 'invertir_lado', 'contabilidad')

        logger.info(
            "Starting reconciliation: %d extracto, %d contabilidad, "
            "max_dias=%d, tolerancia=%.2f, invertir_lado=%s",
            len(extracto), len(contabilidad),
            config.max_dias_diferencia, config.tolerancia_monto,
            invertir_lado,
        )

        if invertir_lado == "extracto":
            # Invert extracto side only (e.g., Occidente, Popular, ...)
            ctb_prepared = []
            for m in contabilidad:
                c = copy.copy(m)
                c.naturaleza_matching = c.naturaleza
                ctb_prepared.append(c)
            invertir_naturaleza_extracto(extracto, invertir=True)
        elif invertir_lado == "ninguno":
            # No inversion on either side
            ctb_prepared = []
            for m in contabilidad:
                c = copy.copy(m)
                c.naturaleza_matching = c.naturaleza
                ctb_prepared.append(c)
            for ext in extracto:
                ext.naturaleza_matching = ext.naturaleza
        else:  # "contabilidad" — current behavior
            ctb_prepared = invertir_naturaleza(contabilidad, config.invertir_naturaleza)
            for ext in extracto:
                ext.naturaleza_matching = ext.naturaleza

        remaining_ext = list(extracto)
        remaining_ctb = list(ctb_prepared)
        all_matches = []

        # Level 1: Exact match (date + value + nature)
        matches_1, remaining_ext, remaining_ctb = match_exacto(
            remaining_ext, remaining_ctb, config
        )
        all_matches.extend(matches_1)
        logger.info("Level 1 (exact): %d matches, %d ext remaining, %d ctb remaining",
                     len(matches_1), len(remaining_ext), len(remaining_ctb))

        # Level 2: Flexible date match
        matches_2, remaining_ext, remaining_ctb = match_fecha_flexible(
            remaining_ext, remaining_ctb, config
        )
        all_matches.extend(matches_2)
        logger.info("Level 2 (flex date): %d matches, %d ext remaining, %d ctb remaining",
                     len(matches_2), len(remaining_ext), len(remaining_ctb))

        # Level 3: N:M group match
        matches_3, remaining_ext, remaining_ctb = match_grupo(
            remaining_ext, remaining_ctb, config
        )
        all_matches.extend(matches_3)
        logger.info("Level 3 (N:M group): %d matches, %d ext remaining, %d ctb remaining",
                     len(matches_3), len(remaining_ext), len(remaining_ctb))

        # Level 4: Classify unmatched + cuadre
        cuadre = None
        if info_extracto:
            cuadre = clasificar_no_conciliados(
                remaining_ext, remaining_ctb, info_extracto, saldo_libros
            )

        # Compute summary
        total_movements = len(extracto) + len(contabilidad)
        total_matched_movements = self._count_matched_movements(all_matches)
        pct = (total_matched_movements / total_movements * 100) if total_movements > 0 else 0

        resumen = ResumenConciliacion(
            movimientos_extracto=len(extracto),
            movimientos_contabilidad=len(contabilidad),
            conciliados_nivel_1=len(matches_1),
            conciliados_nivel_2=len(matches_2),
            conciliados_nivel_3=len(matches_3),
            total_conciliados=len(matches_1) + len(matches_2) + len(matches_3),
            no_conciliados_extracto=len(remaining_ext),
            no_conciliados_contabilidad=len(remaining_ctb),
            porcentaje_conciliacion=round(pct, 2),
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Reconciliation complete in %dms: %d matches (L1:%d L2:%d L3:%d), "
            "%d unmatched ext, %d unmatched ctb, %.1f%% conciliado",
            elapsed_ms, resumen.total_conciliados,
            resumen.conciliados_nivel_1, resumen.conciliados_nivel_2, resumen.conciliados_nivel_3,
            resumen.no_conciliados_extracto, resumen.no_conciliados_contabilidad,
            resumen.porcentaje_conciliacion,
        )

        return ConciliacionResult(
            matches=all_matches,
            no_conciliados_extracto=remaining_ext,
            no_conciliados_contabilidad=remaining_ctb,
            resumen=resumen,
            cuadre_final=cuadre,
        )

    def _count_matched_movements(self, matches: list) -> int:
        """Count total individual movements matched (not match count)."""
        count = 0
        for m in matches:
            # Extracto side
            if isinstance(m.movimiento_extracto, list):
                count += len(m.movimiento_extracto)
            else:
                count += 1
            # Contabilidad side
            if isinstance(m.movimiento_contabilidad, list):
                count += len(m.movimiento_contabilidad)
            else:
                count += 1
        return count
