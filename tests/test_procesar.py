"""Unit tests for POST /api/v1/conciliaciones/procesar."""

import json
from datetime import date as dt_date
from unittest.mock import MagicMock

import pytest
from unittest.mock import patch

from concilia_engine.models import CuadreFinal, ResumenConciliacion


MOCK_MOVIMIENTOS_JSON = json.dumps([
    {
        "fecha": "01-03-2026",
        "codigo_movimiento": "TRX001",
        "debito": 0,
        "credito": 500000,
        "saldo": 1750000,
        "conciliado": False,
    },
    {
        "fecha": "02-03-2026",
        "codigo_movimiento": "TRX002",
        "debito": 100000,
        "credito": 0,
        "saldo": 1650000,
        "conciliado": False,
    },
])


def _make_mock_pipeline_result(diferencia=0.0, periodo_inicio=None, periodo_fin=None, numero_cuenta="",
                               movs_extracto=3, movs_contabilidad=2, matches=None):
    if periodo_inicio is None:
        periodo_inicio = dt_date(2026, 3, 1)
    if periodo_fin is None:
        periodo_fin = dt_date(2026, 3, 31)
    if matches is None:
        matches = []

    total_movs = movs_extracto + movs_contabilidad
    conciliados_count = 2 if movs_extracto >= 2 and movs_contabilidad >= 2 else 0
    no_conc_ext = movs_extracto - min(conciliados_count, movs_extracto)
    no_conc_ctb = movs_contabilidad - min(conciliados_count, movs_contabilidad)

    return {
        "parse_result": MagicMock(
            parser_utilizado="bancolombia",
            parser_fallback=False,
            info_extracto=MagicMock(
                periodo_inicio=periodo_inicio,
                periodo_fin=periodo_fin,
                numero_cuenta=numero_cuenta,
                saldo_anterior=10000.0,
                saldo_final=10000.0,
            ),
            movimientos=[],
        ),
        "match_result": MagicMock(
            resumen=ResumenConciliacion(
                movimientos_extracto=movs_extracto,
                movimientos_contabilidad=movs_contabilidad,
                conciliados_nivel_1=conciliados_count,
                conciliados_nivel_2=0,
                conciliados_nivel_3=0,
                total_conciliados=conciliados_count,
                no_conciliados_extracto=no_conc_ext,
                no_conciliados_contabilidad=no_conc_ctb,
                porcentaje_conciliacion=round((conciliados_count * 2 / total_movs * 100), 2) if total_movs > 0 else 0,
            ),
            cuadre_final=CuadreFinal(
                saldo_libros=10000,
                saldo_extracto=10000,
                partidas_libros=0,
                partidas_extracto=0,
                cheques_no_cobrados=0,
                consignaciones_transito=0,
                suma_iguales_libros=10000,
                suma_iguales_extracto=10000,
                diferencia=diferencia,
            ),
            matches=matches,
            no_conciliados_extracto=[],
            no_conciliados_contabilidad=[],
        ),
        "informe": {"status": "success", "resumen": {}},
        "periodo": "202603",
        "elapsed_ms": 150,
    }

PATH = "/api/v1/conciliaciones/procesar"


class TestProcesar:
    """Tests for the public /procesar endpoint."""

    async def test_procesar_completada(self, mock_pipeline, client):
        """Successful reconciliation — estado completada."""
        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "cuenta_bancaria": "{}",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estado"] == "completada"
        assert data["periodo"] == "202603"
        assert data["cuadre_diferencia"] == 0.0
        assert data["resumen"]["total_movimientos"] == 5
        assert data["resumen"]["conciliados"] == 4
        assert data["resumen"]["no_conciliados"] == 1
        assert data["resumen"]["porcentaje_conciliacion"] == 80.0
        assert data["metricas"]["tiempo_total_ms"] == 150
        assert "movimientos_detalle" in data
        assert "advertencias" in data
        assert len(data["movimientos_detalle"]) == 2
        assert all(isinstance(m["conciliado"], bool) for m in data["movimientos_detalle"])

    async def test_procesar_no_completada(self, mock_pipeline, client):
        """Mismatch — estado no_completada with cuadre_diferencia in advertencias."""
        mock_pipeline.return_value = _make_mock_pipeline_result(diferencia=5000.0)

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estado"] == "no_completada"
        assert data["cuadre_diferencia"] == 5000.0
        cuadre_warnings = [w for w in data["advertencias"] if w["tipo"] == "cuadre_diferencia"]
        assert len(cuadre_warnings) == 1
        assert cuadre_warnings[0]["diferencia"] == 5000.0

    async def test_procesar_periodo_invalido(self, client):
        """Invalid periodo format → 422."""
        resp = await client.post(
            PATH,
            data={
                "periodo": "badformat",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["estado"] == "error"
        assert data["detail"]["error"]["codigo"] == "VALIDACION_ERROR"

    async def test_procesar_movimientos_vacio(self, client):
        """Empty movimientos_detalle → 422."""
        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": "[]",
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["estado"] == "error"
        assert "no puede venir vacio" in data["detail"]["error"]["mensaje"]

    async def test_procesar_json_invalido(self, client):
        """Invalid JSON in movimientos_detalle → 422."""
        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": "not json",
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["error"]["codigo"] == "VALIDACION_ERROR"

    async def test_procesar_sin_auth(self, client):
        """Endpoint is accessible without any auth header."""
        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": "not json",
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
            headers={},
        )
        assert resp.status_code not in (401, 403)

    async def test_procesar_fecha_invalida(self, mock_pipeline, client):
        """Invalid fecha format → 422."""
        bad_json = json.dumps([
            {
                "fecha": "2026/03/01",
                "codigo_movimiento": "TRX",
                "debito": 0,
                "credito": 100,
                "saldo": 1000,
                "conciliado": False,
            }
        ])

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": bad_json,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["estado"] == "error"
        assert "Formato de fecha invalido" in data["detail"]["error"]["mensaje"]

    async def test_procesar_archivo_grande(self, client):
        """File exceeding MAX_FILE_SIZE → 400."""
        with patch("main.MAX_FILE_SIZE", 1):
            resp = await client.post(
                PATH,
                data={
                    "periodo": "202603",
                    "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
                },
                files={
                    "extracto": ("extracto.pdf", b"a" * 100, "application/pdf"),
                },
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"]["codigo"] == "ARCHIVO_MUY_GRANDE"

    async def test_procesar_pipeline_value_error(self, mock_pipeline, client):
        """ValueError from pipeline → 422."""
        mock_pipeline.side_effect = ValueError("No se pudo parsear")

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["error"]["codigo"] == "PROCESAMIENTO_ERROR"

    async def test_procesar_pipeline_generic_error(self, mock_pipeline, client):
        """Generic Exception from pipeline → 500."""
        mock_pipeline.side_effect = Exception("Unexpected failure")

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 500
        data = resp.json()
        assert data["detail"]["error"]["codigo"] == "ERROR_INTERNO"

    async def test_procesar_periodo_mismatch(self, mock_pipeline, client):
        """Period outside PDF range → 422 VALIDACION_PERIODO."""
        from datetime import date as dt_date
        mock_pipeline.return_value = _make_mock_pipeline_result(
            periodo_inicio=dt_date(2026, 1, 1),
            periodo_fin=dt_date(2026, 1, 31),
        )

        resp = await client.post(
            PATH,
            data={
                "periodo": "202606",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["estado"] == "error"
        assert data["detail"]["error"]["codigo"] == "VALIDACION_PERIODO"
        assert "no concuerda con el extracto" in data["detail"]["error"]["mensaje"]
        assert data["detail"]["error"]["periodo_recibido"] == "202606"
        assert data["detail"]["error"]["periodo_extraido"] == "202601"

    async def test_procesar_periodo_overlap_multimes(self, mock_pipeline, client):
        """Period overlaps multi-month PDF range → 200 OK."""
        from datetime import date as dt_date
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0,
            periodo_inicio=dt_date(2026, 1, 15),
            periodo_fin=dt_date(2026, 2, 15),
        )

        resp = await client.post(
            PATH,
            data={
                "periodo": "202602",
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        assert resp.json()["estado"] == "completada"

    async def test_procesar_cuenta_mismatch(self, mock_pipeline, client):
        """Account number does not match PDF → 422 VALIDACION_CUENTA."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0,
            numero_cuenta="938554490",
        )

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "cuenta_bancaria": json.dumps({"numero_cuenta_bancaria": "999888777"}),
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["estado"] == "error"
        assert data["detail"]["error"]["codigo"] == "VALIDACION_CUENTA"
        assert "no concuerda con el extracto" in data["detail"]["error"]["mensaje"]
        assert data["detail"]["error"]["cuenta_recibida"] == "999888777"
        assert data["detail"]["error"]["cuenta_extraida"] == "938554490"

    async def test_procesar_cuenta_match(self, mock_pipeline, client):
        """Account number matches → 200 OK."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0,
            numero_cuenta="938554490",
        )

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "cuenta_bancaria": json.dumps({"numero_cuenta_bancaria": "938554490"}),
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        assert resp.json()["estado"] == "completada"

    async def test_procesar_advertencias_saldos(self, mock_pipeline, client):
        """Saldo mismatch generates warnings but doesn't block."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0,
        )

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "cuenta_bancaria": json.dumps({
                    "numero_cuenta_bancaria": "",
                    "saldo_anterior_periodo": 5000.0,
                    "saldo_actual_periodo": 5000.0,
                }),
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estado"] == "completada"
        tipos = [w["tipo"] for w in data["advertencias"]]
        assert "saldo_anterior" in tipos
        assert "saldo_actual" in tipos

    async def test_procesar_advertencias_saldos_coinciden(self, mock_pipeline, client):
        """Matching saldos → no warnings."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0,
            movs_extracto=2,
        )

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "cuenta_bancaria": json.dumps({
                    "numero_cuenta_bancaria": "",
                    "saldo_anterior_periodio": 10000.0,
                    "saldo_actual_periodo": 10000.0,
                }),
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estado"] == "completada"
        assert data["advertencias"] == []

    async def test_procesar_sin_periodo(self, mock_pipeline, client):
        """Request without periodo — auto-detected from PDF, returned in response."""
        resp = await client.post(
            PATH,
            data={
                "movimientos_detalle": MOCK_MOVIMIENTOS_JSON,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estado"] == "completada"
        assert data["periodo"] == "202603"

    async def test_procesar_movimiento_cero_omitido(self, mock_pipeline, client):
        """Movement with both debito=0 and credito=0 is marked as not conciliado."""
        three_json = json.dumps([
            {"fecha": "01-03-2026", "codigo_movimiento": "TRX001", "debito": 0, "credito": 500, "saldo": 0, "conciliado": False},
            {"fecha": "02-03-2026", "codigo_movimiento": "ZERO", "debito": 0, "credito": 0, "saldo": 0, "conciliado": False},
            {"fecha": "03-03-2026", "codigo_movimiento": "TRX003", "debito": 100, "credito": 0, "saldo": 0, "conciliado": False},
        ])

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": three_json,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["movimientos_detalle"]) == 3
        # The ZERO movement should be present and have conciliado=False
        assert data["movimientos_detalle"][1]["codigo_movimiento"] == "ZERO"
        assert data["movimientos_detalle"][1]["conciliado"] is False

    async def test_procesar_creditos_negativos(self, mock_pipeline, client):
        """Negative credits are processed (abs value used) — no se omiten."""
        json_neg = json.dumps([
            {"fecha": "08-01-2025", "codigo_movimiento": "PG001", "debito": 0, "credito": -250000000, "saldo": 39891844847.62, "conciliado": False},
            {"fecha": "27-01-2025", "codigo_movimiento": "PG002", "debito": 118886961, "credito": 0, "saldo": 39637580871.62, "conciliado": False},
            {"fecha": "27-01-2025", "codigo_movimiento": "PG003", "debito": 0, "credito": -600117, "saldo": 39432313627.62, "conciliado": False},
        ])

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": json_neg,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        movs = data["movimientos_detalle"]
        assert len(movs) == 3
        assert all(isinstance(m["conciliado"], bool) for m in movs)
        assert movs[0]["codigo_movimiento"] == "PG001"
        assert movs[1]["codigo_movimiento"] == "PG002"
        assert movs[2]["codigo_movimiento"] == "PG003"

    async def test_procesar_debito_negativo(self, mock_pipeline, client):
        """Negative debits are also processed with abs()."""
        json_neg = json.dumps([
            {"fecha": "08-01-2025", "codigo_movimiento": "DB001", "debito": -50000, "credito": 0, "saldo": 1000000, "conciliado": False},
        ])

        resp = await client.post(
            PATH,
            data={
                "periodo": "202603",
                "movimientos_detalle": json_neg,
            },
            files={
                "extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf"),
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["movimientos_detalle"]) == 1
        assert data["movimientos_detalle"][0]["codigo_movimiento"] == "DB001"
        assert data["movimientos_detalle"][0]["nota"]
        assert "No conciliado" in data["movimientos_detalle"][0]["nota"]

    async def test_procesar_nota_conciliado_exacto(self, mock_pipeline, client):
        """Matched movement has nota with EXT id and level info."""
        from datetime import date as _dt_date
        from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto

        ext = MovimientoExtracto(id="EXT-0007", fecha=_dt_date(2026, 3, 1),
                                  valor=500000, naturaleza="credito",
                                  descripcion="PAGO A TERCEROS AVAL")
        ctb = MovimientoContable(id="CTB-0001", fecha=_dt_date(2026, 3, 1),
                                  valor=500000, naturaleza="credito",
                                  descripcion="TRX001")
        match = Match(nivel=1, confianza=0.95, movimiento_extracto=ext,
                       movimiento_contabilidad=ctb, tipo="exacto")

        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=2, matches=[match],
        )

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": MOCK_MOVIMIENTOS_JSON},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        movs = resp.json()["movimientos_detalle"]
        assert len(movs) == 2
        assert movs[0]["conciliado"] is True
        assert "Conciliado con movimiento del extracto número EXT-0007" in movs[0]["nota"]
        assert "nivel 1" in movs[0]["nota"]
        assert movs[1]["conciliado"] is False
        assert "No conciliado" in movs[1]["nota"]

    async def test_procesar_nota_conciliado_fecha_flexible(self, mock_pipeline, client):
        """Matched at level 2 shows dias_diferencia in nota."""
        from datetime import date as _dt_date
        from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto

        ext = MovimientoExtracto(id="EXT-0010", fecha=_dt_date(2026, 3, 4),
                                  valor=100000, naturaleza="debito",
                                  descripcion="PAGO TERCERO")
        ctb = MovimientoContable(id="CTB-0002", fecha=_dt_date(2026, 3, 2),
                                  valor=100000, naturaleza="debito",
                                  descripcion="TRX002")
        match = Match(nivel=2, confianza=0.80, movimiento_extracto=ext,
                       movimiento_contabilidad=ctb, tipo="fecha_flexible",
                       dias_diferencia=2)

        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=2, matches=[match],
        )

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": MOCK_MOVIMIENTOS_JSON},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        movs = resp.json()["movimientos_detalle"]
        assert movs[1]["conciliado"] is True
        assert "nivel 2" in movs[1]["nota"]
        assert "2 dias de diferencia" in movs[1]["nota"]

    async def test_procesar_nota_multiple_candidates(self, mock_pipeline, client):
        """Level 1 with multiple_candidates shows that in nota."""
        from datetime import date as _dt_date
        from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto

        ext = MovimientoExtracto(id="EXT-0007", fecha=_dt_date(2026, 3, 1),
                                  valor=500000, naturaleza="credito",
                                  descripcion="PAGO A TERCEROS")
        ctb = MovimientoContable(id="CTB-0001", fecha=_dt_date(2026, 3, 1),
                                  valor=500000, naturaleza="credito",
                                  descripcion="TRX001")
        match = Match(nivel=1, confianza=0.85, movimiento_extracto=ext,
                       movimiento_contabilidad=ctb, tipo="exacto",
                       multiple_candidates=True)

        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=2, matches=[match],
        )

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": MOCK_MOVIMIENTOS_JSON},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        movs = resp.json()["movimientos_detalle"]
        assert "multiples candidatos" in movs[0]["nota"]

    async def test_procesar_nota_vacia_cero(self, mock_pipeline, client):
        """Zero-value movement has nota = \"\"."""
        json_zero = json.dumps([
            {"fecha": "01-03-2026", "codigo_movimiento": "ZERO", "debito": 0, "credito": 0, "saldo": 0, "conciliado": False},
        ])

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": json_zero},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        movs = resp.json()["movimientos_detalle"]
        assert len(movs) == 1
        assert movs[0]["nota"] == ""

    async def test_procesar_advertencia_movimientos_insuficientes(self, mock_pipeline, client):
        """Warning when contabilidad < extracto."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=10, movs_contabilidad=2,
        )

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": MOCK_MOVIMIENTOS_JSON},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        advertencias = resp.json()["advertencias"]
        insuficientes = [w for w in advertencias if w["tipo"] == "movimientos_insuficientes"]
        assert len(insuficientes) == 1
        assert insuficientes[0]["movimientos_contables"] == 2
        assert insuficientes[0]["movimientos_extracto"] == 10

    async def test_procesar_advertencia_sin_movimientos_insuficientes(self, mock_pipeline, client):
        """No warning when contabilidad >= extracto."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=2, movs_contabilidad=2,
        )

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": MOCK_MOVIMIENTOS_JSON},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        advertencias = resp.json()["advertencias"]
        insuficientes = [w for w in advertencias if w["tipo"] == "movimientos_insuficientes"]
        assert len(insuficientes) == 0

    async def test_procesar_advertencia_movimientos_duplicados(self, mock_pipeline, client):
        """Warning when duplicate movements detected in contabilidad."""
        json_dup = json.dumps([
            {"fecha": "01-03-2026", "codigo_movimiento": "DUP001", "debito": 100000, "credito": 0, "saldo": 0, "conciliado": False},
            {"fecha": "01-03-2026", "codigo_movimiento": "DUP002", "debito": 100000, "credito": 0, "saldo": 0, "conciliado": False},
        ])

        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=2, movs_contabilidad=2,
        )

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": json_dup},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        advertencias = resp.json()["advertencias"]
        duplicados = [w for w in advertencias if w["tipo"] == "movimientos_duplicados"]
        assert len(duplicados) == 1
        assert duplicados[0]["grupos_duplicados"] == 1
        assert duplicados[0]["movimientos_afectados"] == 2

    async def test_procesar_reversion_excluida(self, mock_pipeline, client):
        """Reversal pair excluded from matching and duplicate detection."""
        from datetime import date as _rev_date

        json_rev = json.dumps([
            {
                "fecha": "27-01-2025",
                "codigo_movimiento": "ORI001",
                "debito": 0,
                "credito": 118886961,
                "saldo": 0,
                "conciliado": False,
                "codig_cp_contable": "NCO-001",
                "cons_cp_contable": None,
            },
            {
                "fecha": "27-01-2025",
                "codigo_movimiento": "REV001",
                "debito": 0,
                "credito": -118886961,
                "saldo": 0,
                "conciliado": False,
                "codig_cp_contable": "NCO-002",
                "cons_cp_contable": "NCO-001",
            },
        ])

        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=1, movs_contabilidad=2,
            periodo_inicio=_rev_date(2025, 1, 1), periodo_fin=_rev_date(2025, 1, 31),
        )

        resp = await client.post(PATH,
            data={"periodo": "202501", "movimientos_detalle": json_rev},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        data = resp.json()
        movs = data["movimientos_detalle"]

        # Original movement: excluded from matching, nota mentions it was cancelled
        assert movs[0]["codigo_movimiento"] == "ORI001"
        assert movs[0]["conciliado"] is False
        assert movs[0]["nota"] == "ORI001 - Comprobante excluido por estado anulado"
        assert movs[0].get("codig_cp_contable") == "NCO-001"
        assert movs[0].get("cons_cp_contable") is None

        # Reversal movement: excluded from matching, nota references original comprobante
        assert movs[1]["codigo_movimiento"] == "REV001"
        assert movs[1]["conciliado"] is False
        assert movs[1]["nota"] == "REV001 - Comprobante excluido por reversión de movimiento (NCO-001)"
        assert movs[1].get("codig_cp_contable") == "NCO-002"
        assert movs[1].get("cons_cp_contable") == "NCO-001"

        # No duplicados warning since reversal pair is excluded
        advertencias = data["advertencias"]
        duplicados = [w for w in advertencias if w["tipo"] == "movimientos_duplicados"]
        assert len(duplicados) == 0

    async def test_procesar_advertencia_intereses(self, mock_pipeline, client):
        """Warning when extract has unmatched INTERESES LIQUIDADOS."""
        from datetime import date as _dt_date
        from concilia_engine.models import MovimientoExtracto

        mock_pipeline.return_value = _make_mock_pipeline_result(diferencia=0.0, movs_extracto=2)
        mock_pipeline.return_value["match_result"].no_conciliados_extracto = [
            MovimientoExtracto(id="EXT-0001", fecha=_dt_date(2026, 3, 2),
                                valor=4311124.16, naturaleza="credito",
                                descripcion="INTERESES LIQUIDADOS"),
            MovimientoExtracto(id="EXT-0002", fecha=_dt_date(2026, 3, 3),
                                valor=4311587.16, naturaleza="credito",
                                descripcion="INTERESES LIQUIDADOS"),
        ]

        resp = await client.post(PATH,
            data={"periodo": "202603", "movimientos_detalle": MOCK_MOVIMIENTOS_JSON},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        advertencias = resp.json()["advertencias"]
        intereses = [w for w in advertencias if w["tipo"] == "intereses_no_contabilizados"]
        assert len(intereses) == 1
        assert intereses[0]["intereses_sin_conciliar"] == 2

    async def test_procesar_debito_reversion_debito(self, mock_pipeline, client):
        """Scenario: debit → reversal → debit real. Only the last debit conciliates."""
        from datetime import date as _dt_date
        from concilia_engine.models import Match, MovimientoContable, MovimientoExtracto

        json_tercero = json.dumps([
            {
                "fecha": "27-01-2025",
                "codigo_movimiento": "DEB001",
                "debito": 118886961,
                "credito": 0,
                "saldo": 118886961,
                "conciliado": False,
                "codig_cp_contable": "NCO-001",
                "cons_cp_contable": None,
            },
            {
                "fecha": "27-01-2025",
                "codigo_movimiento": "REV001",
                "debito": 0,
                "credito": -118886961,
                "saldo": 0,
                "conciliado": False,
                "codig_cp_contable": "NCO-002",
                "cons_cp_contable": "NCO-001",
            },
            {
                "fecha": "27-01-2025",
                "codigo_movimiento": "DEB002",
                "debito": 118886961,
                "credito": 0,
                "saldo": 118886961,
                "conciliado": False,
                "codig_cp_contable": "NCO-003",
                "cons_cp_contable": None,
            },
        ])

        # Engine only sees DEB002 (DEB001 cancelled, REV001 reversal) — matches with EXT
        ext = MovimientoExtracto(id="EXT-0020", fecha=_dt_date(2025, 1, 27),
                                  valor=118886961, naturaleza="debito",
                                  descripcion="PAGO PROVEEDOR XYZ")
        ctb = MovimientoContable(id="CTB-0003", fecha=_dt_date(2025, 1, 27),
                                  valor=118886961, naturaleza="debito",
                                  descripcion="DEB002")
        match = Match(nivel=1, confianza=0.98, movimiento_extracto=ext,
                       movimiento_contabilidad=ctb, tipo="exacto")

        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0, movs_extracto=1, matches=[match],
            periodo_inicio=_dt_date(2025, 1, 1), periodo_fin=_dt_date(2025, 1, 31),
        )

        resp = await client.post(PATH,
            data={"periodo": "202501", "movimientos_detalle": json_tercero},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

        assert resp.status_code == 200
        data = resp.json()
        movs = data["movimientos_detalle"]
        assert len(movs) == 3

        # DEB001: original cancelled → false, "estado anulado"
        assert movs[0]["codigo_movimiento"] == "DEB001"
        assert movs[0]["conciliado"] is False
        assert movs[0]["nota"] == "DEB001 - Comprobante excluido por estado anulado"

        # REV001: reversal → false, references original comprobante
        assert movs[1]["codigo_movimiento"] == "REV001"
        assert movs[1]["conciliado"] is False
        assert movs[1]["nota"] == "REV001 - Comprobante excluido por reversión de movimiento (NCO-001)"

        # DEB002: real debit → conciliado=true, matched by engine
        assert movs[2]["codigo_movimiento"] == "DEB002"
        assert movs[2]["conciliado"] is True
        assert "DEB002 - Conciliado con movimiento del extracto número EXT-0020" in movs[2]["nota"]
        assert "monto 118,886,961" in movs[2]["nota"]
