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


def _make_mock_pipeline_result(diferencia=0.0, periodo_inicio=None, periodo_fin=None, numero_cuenta=""):
    if periodo_inicio is None:
        periodo_inicio = dt_date(2026, 3, 1)
    if periodo_fin is None:
        periodo_fin = dt_date(2026, 3, 31)

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
        ),
        "match_result": MagicMock(
            resumen=ResumenConciliacion(
                movimientos_extracto=3,
                movimientos_contabilidad=2,
                conciliados_nivel_1=2,
                conciliados_nivel_2=0,
                conciliados_nivel_3=0,
                total_conciliados=2,
                no_conciliados_extracto=1,
                no_conciliados_contabilidad=0,
                porcentaje_conciliacion=80.0,
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
            matches=[],
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
        """Mismatch — estado no_completada."""
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
        assert len(data["advertencias"]) == 2
        assert data["advertencias"][0]["tipo"] == "saldo_anterior"
        assert data["advertencias"][1]["tipo"] == "saldo_actual"

    async def test_procesar_advertencias_saldos_coinciden(self, mock_pipeline, client):
        """Matching saldos → no warnings."""
        mock_pipeline.return_value = _make_mock_pipeline_result(
            diferencia=0.0,
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
