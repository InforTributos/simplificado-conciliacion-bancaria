from datetime import date as dt_date
from unittest.mock import MagicMock, patch

import pytest
import json
from httpx import ASGITransport, AsyncClient

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


def make_mock_pipeline_result(diferencia=0.0, periodo_inicio=None, periodo_fin=None, numero_cuenta=""):
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


@pytest.fixture
async def client():
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_pipeline():
    with patch("main.ejecutar_pipeline_conciliacion") as mock:
        mock.return_value = make_mock_pipeline_result()
        yield mock
