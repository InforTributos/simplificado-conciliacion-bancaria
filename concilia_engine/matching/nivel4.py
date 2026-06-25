"""Nivel 4: Unmatched classification and cuadre (balance) formula."""

from __future__ import annotations

from concilia_engine.models import (
    CuadreFinal,
    InfoExtracto,
    MovimientoContable,
    MovimientoExtracto,
)


def clasificar_no_conciliados(
    no_conciliados_extracto: list[MovimientoExtracto],
    no_conciliados_contabilidad: list[MovimientoContable],
    info_extracto: InfoExtracto,
    saldo_libros: float | None = None,
) -> CuadreFinal:
    """Calculate the cuadre (balance) formula for unmatched items.

    Formula (bank reconciliation):
    Saldo Libros + (Créditos Extracto No Conciliados - Débitos Extracto No Conciliados)
    = Saldo Extracto + (Consignaciones en Tránsito - Cheques No Cobrados)

    Where:
      Consignaciones en Tránsito = Créditos contabilidad no conciliados
      Cheques No Cobrados       = Débitos contabilidad no conciliados
    """
    saldo_extracto = info_extracto.saldo_final

    # Partidas del lado contabilidad (libros)
    partidas_libros_debito = sum(
        m.valor for m in no_conciliados_contabilidad
        if m.naturaleza_matching == "debito"
    )
    partidas_libros_credito = sum(
        m.valor for m in no_conciliados_contabilidad
        if m.naturaleza_matching == "credito"
    )

    # Cheques no cobrados = contabilidad debitos no conciliados (post-inversion)
    # These are payments registered in books but not yet in the bank
    cheques_no_cobrados = partidas_libros_debito

    # Consignaciones en transito = contabilidad creditos no conciliados (post-inversion)
    # These are deposits registered in books but not yet in the bank
    consignaciones_transito = partidas_libros_credito

    # Partidas del lado extracto (banco)
    partidas_extracto_debito = sum(
        m.valor for m in no_conciliados_extracto
        if m.naturaleza == "debito"
    )
    partidas_extracto_credito = sum(
        m.valor for m in no_conciliados_extracto
        if m.naturaleza == "credito"
    )
    # Net partidas: créditos incrementan el saldo, débitos lo disminuyen
    partidas_extracto = partidas_extracto_credito - partidas_extracto_debito

    # Net partidas del lado libros: créditos (consignaciones tránsito) incrementan, débitos (cheques no cobrados) disminuyen
    partidas_libros = partidas_libros_credito - partidas_libros_debito

    # Use provided saldo_libros or estimate from extracto
    if saldo_libros is None:
        saldo_libros = saldo_extracto  # Approximate

    # Partidas del extracto (creditos/debitos no conciliados del banco) ajustan el saldo libros
    # Partidas de libros (consignaciones transito/cheques no cobrados) ajustan el saldo extracto
    suma_iguales_libros = saldo_libros + partidas_extracto
    suma_iguales_extracto = saldo_extracto + partidas_libros
    diferencia = round(abs(suma_iguales_libros - suma_iguales_extracto), 2)

    return CuadreFinal(
        saldo_libros=round(saldo_libros, 2),
        saldo_extracto=round(saldo_extracto, 2),
        partidas_libros=round(partidas_libros, 2),
        partidas_extracto=round(partidas_extracto, 2),
        cheques_no_cobrados=round(cheques_no_cobrados, 2),
        consignaciones_transito=round(consignaciones_transito, 2),
        suma_iguales_libros=round(suma_iguales_libros, 2),
        suma_iguales_extracto=round(suma_iguales_extracto, 2),
        diferencia=diferencia,
    )
