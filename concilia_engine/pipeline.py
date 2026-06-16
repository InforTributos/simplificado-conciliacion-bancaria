"""Shared reconciliation pipeline — no DB dependency.

Extracted from ConciliacionService.execute() so both the authenticated
multi-tenant endpoint and the public POST /procesar endpoint can reuse
the same PDF-parsing + matching + reporting core.
"""

from __future__ import annotations

import logging
import time

from concilia_engine.config import LLMConfig, MatchConfig, ParseConfig
from concilia_engine.matching import MatchingEngine
from concilia_engine.models import InfoContabilidad, MovimientoContable
from concilia_engine.parsers.router import ParserRouter
from concilia_engine.report import generar_informe

logger = logging.getLogger(__name__)


def ejecutar_pipeline_conciliacion(
    extracto_bytes: bytes,
    extracto_filename: str,
    movimientos_contables: list[MovimientoContable],
    periodo: str | None,
    config: MatchConfig,
    llm_config: LLMConfig,
    saldo_libros: float | None = None,
) -> dict:
    """Run the full reconciliation pipeline: parse PDF, match, and report.

    Parameters
    ----------
    extracto_bytes : bytes
        Raw bytes of the bank extract PDF file.
    extracto_filename : str
        Original filename for format detection.
    movimientos_contables : list[MovimientoContable]
        Accounting movements (already parsed from Excel or JSON).
    periodo : str | None
        Period in AAAAMM format. If None, it is inferred from the extract.
    config : MatchConfig
        Matching configuration (tolerance, max days, etc.).
    llm_config : LLMConfig
        LLM configuration for fallback and vision parsing.
    saldo_libros : float | None
        Accounting balance at the start of the period. If None, defaults
        to the extract's ``saldo_anterior``.

    Returns
    -------
    dict
        Keys: ``parse_result``, ``match_result``, ``informe``, ``periodo``, ``elapsed_ms``.
    """
    start_time = time.time()

    # Parse bank extract
    parse_config = ParseConfig(forzar_llm=config.forzar_llm)
    parser_router = ParserRouter()
    parse_result = parser_router.parse_extracto(
        extracto_bytes, extracto_filename, parse_config, llm_config
    )

    # Determine periodo
    if not periodo:
        p_inicio = parse_result.info_extracto.periodo_inicio
        periodo = f"{p_inicio.year}{p_inicio.month:02d}"

    # Resolve saldo_libros default
    if saldo_libros is None:
        saldo_libros = parse_result.info_extracto.saldo_anterior

    # Execute matching
    engine = MatchingEngine()
    match_result = engine.conciliar(
        parse_result.movimientos,
        movimientos_contables,
        config,
        info_extracto=parse_result.info_extracto,
        saldo_libros=saldo_libros,
    )

    # Generate report
    if movimientos_contables:
        info_ctb = InfoContabilidad(
            periodo_inicio=movimientos_contables[0].fecha,
            periodo_fin=movimientos_contables[-1].fecha,
            total_registros=len(movimientos_contables),
        )
    else:
        info_ctb = InfoContabilidad(
            periodo_inicio=parse_result.info_extracto.periodo_inicio,
            periodo_fin=parse_result.info_extracto.periodo_fin,
            total_registros=0,
        )

    informe = generar_informe(match_result, parse_result.info_extracto, info_ctb)

    elapsed_ms = int((time.time() - start_time) * 1000)

    logger.info(
        "Pipeline completed in %dms: %d matches, %.1f%% conciliado, diff=%.2f",
        elapsed_ms,
        match_result.resumen.total_conciliados if match_result.resumen else 0,
        match_result.resumen.porcentaje_conciliacion if match_result.resumen else 0,
        match_result.cuadre_final.diferencia if match_result.cuadre_final else 0,
    )

    return {
        "parse_result": parse_result,
        "match_result": match_result,
        "informe": informe,
        "periodo": periodo,
        "elapsed_ms": elapsed_ms,
    }
