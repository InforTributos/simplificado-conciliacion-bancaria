"""Report generator — consolidates reconciliation results into JSON structure."""

from __future__ import annotations

from concilia_engine.models import (
    ConciliacionResult,
    InfoContabilidad,
    InfoExtracto,
    Match,
    MovimientoContable,
    MovimientoExtracto,
)


def generar_informe(
    result: ConciliacionResult,
    info_extracto: InfoExtracto,
    info_contabilidad: InfoContabilidad,
) -> dict:
    """Generate the full JSON report matching the API response spec."""
    return {
        "status": "success",
        "info_extracto": _serialize_info_extracto(info_extracto),
        "info_contabilidad": _serialize_info_contabilidad(info_contabilidad),
        "resumen": _serialize_resumen(result),
        "conciliaciones": [_serialize_match(m) for m in result.matches],
        "no_conciliados_extracto": [
            _serialize_mov_extracto(m) for m in result.no_conciliados_extracto
        ],
        "no_conciliados_contabilidad": [
            _serialize_mov_contable(m) for m in result.no_conciliados_contabilidad
        ],
        "cuadre_final": _serialize_cuadre(result.cuadre_final) if result.cuadre_final else None,
    }


def _serialize_info_extracto(info: InfoExtracto) -> dict:
    return {
        "banco": info.banco,
        "numero_cuenta": info.numero_cuenta,
        "periodo_inicio": info.periodo_inicio.isoformat(),
        "periodo_fin": info.periodo_fin.isoformat(),
        "saldo_anterior": info.saldo_anterior,
        "saldo_final": info.saldo_final,
    }


def _serialize_info_contabilidad(info: InfoContabilidad) -> dict:
    return {
        "periodo_inicio": info.periodo_inicio.isoformat(),
        "periodo_fin": info.periodo_fin.isoformat(),
        "total_registros": info.total_registros,
    }


def _serialize_resumen(result: ConciliacionResult) -> dict:
    r = result.resumen
    if not r:
        return {}
    return {
        "movimientos_extracto": r.movimientos_extracto,
        "movimientos_contabilidad": r.movimientos_contabilidad,
        "conciliados": {
            "nivel_1_exacto": r.conciliados_nivel_1,
            "nivel_2_fecha_flexible": r.conciliados_nivel_2,
            "nivel_3_grupo": r.conciliados_nivel_3,
            "total": r.total_conciliados,
        },
        "no_conciliados": {
            "solo_extracto": r.no_conciliados_extracto,
            "solo_contabilidad": r.no_conciliados_contabilidad,
        },
        "porcentaje_conciliacion": r.porcentaje_conciliacion,
    }


def _serialize_match(match: Match) -> dict:
    result: dict = {
        "nivel": match.nivel,
        "confianza": match.confianza,
        "tipo": match.tipo,
    }

    if match.dias_diferencia is not None:
        result["dias_diferencia"] = match.dias_diferencia

    if match.multiple_candidates:
        result["multiple_candidates"] = True

    # Extracto side
    if isinstance(match.movimiento_extracto, list):
        result["movimientos_extracto"] = [
            _serialize_mov_extracto(m) for m in match.movimiento_extracto
        ]
    else:
        result["movimiento_extracto"] = _serialize_mov_extracto(match.movimiento_extracto)

    # Contabilidad side
    if isinstance(match.movimiento_contabilidad, list):
        result["movimientos_contabilidad"] = [
            _serialize_mov_contable(m) for m in match.movimiento_contabilidad
        ]
    else:
        result["movimiento_contabilidad"] = _serialize_mov_contable(match.movimiento_contabilidad)

    return result


def _serialize_mov_extracto(mov: MovimientoExtracto) -> dict:
    result = {
        "id": mov.id,
        "fecha": mov.fecha.isoformat(),
        "descripcion": mov.descripcion,
        "valor": mov.valor,
        "naturaleza": mov.naturaleza,
    }
    if mov.referencia:
        result["referencia"] = mov.referencia
    return result


def _serialize_mov_contable(mov: MovimientoContable) -> dict:
    result = {
        "id": mov.id,
        "fecha": mov.fecha.isoformat(),
        "descripcion": mov.descripcion,
        "valor": mov.valor,
        "naturaleza": mov.naturaleza,
    }
    if mov.referencia:
        result["referencia"] = mov.referencia
    if mov.tipo_documento:
        result["tipo_documento"] = mov.tipo_documento
    if mov.codigo_comprobante:
        result["codigo_comprobante"] = mov.codigo_comprobante
    return result


def _serialize_cuadre(cuadre) -> dict:
    return {
        "saldo_libros": cuadre.saldo_libros,
        "saldo_extracto": cuadre.saldo_extracto,
        "partidas_libros": cuadre.partidas_libros,
        "partidas_extracto": cuadre.partidas_extracto,
        "cheques_no_cobrados": cuadre.cheques_no_cobrados,
        "consignaciones_transito": cuadre.consignaciones_transito,
        "suma_iguales_libros": cuadre.suma_iguales_libros,
        "suma_iguales_extracto": cuadre.suma_iguales_extracto,
        "diferencia": cuadre.diferencia,
    }
