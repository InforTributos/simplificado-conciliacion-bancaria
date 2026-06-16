"""Integration/E2E tests — start real server and hit it with httpx."""

import subprocess
import sys
import time

import pytest
from httpx import AsyncClient


@pytest.fixture(scope="module")
def server():
    """Start uvicorn in background, wait for readiness, tear down after tests."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8002"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to be ready
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            import urllib.request
            urllib.request.urlopen("http://127.0.0.1:8002/docs").close()
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("Server did not start in time")

    yield proc

    proc.terminate()
    proc.wait()


@pytest.fixture
async def e2e_client(server):
    """Async client against the running server."""
    async with AsyncClient(base_url="http://127.0.0.1:8002", timeout=30) as ac:
        yield ac


class TestE2EProcesar:
    """Real HTTP calls against running server."""

    async def test_docs_accessible(self, e2e_client):
        """Server is up and docs are reachable."""
        resp = await e2e_client.get("/docs")
        assert resp.status_code == 200

    async def test_missing_extracto(self, e2e_client):
        """Missing required extracto file → 422."""
        resp = await e2e_client.post(
            "/api/v1/conciliaciones/procesar",
            data={"movimientos_detalle": '[]'},
        )
        assert resp.status_code == 422

    async def test_invalid_json_movimientos(self, e2e_client):
        """Invalid movimientos_detalle → 422."""
        resp = await e2e_client.post(
            "/api/v1/conciliaciones/procesar",
            data={"movimientos_detalle": "not-json"},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["error"]["codigo"] == "VALIDACION_ERROR"

    async def test_empty_movimientos(self, e2e_client):
        """Empty movimientos list → 422."""
        resp = await e2e_client.post(
            "/api/v1/conciliaciones/procesar",
            data={"movimientos_detalle": "[]"},
            files={"extracto": ("extracto.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 422

    async def test_procesar_basic(self, e2e_client):
        """Full e2e with valid data — returns 200."""
        import json as _json

        movs = _json.dumps([
            {"fecha": "01-03-2026", "codigo_movimiento": "E2E-001", "debito": 0, "credito": 250000, "saldo": 1750000, "conciliado": False},
        ])

        resp = await e2e_client.post(
            "/api/v1/conciliaciones/procesar",
            data={
                "periodo": "202603",
                "movimientos_detalle": movs,
            },
            files={"extracto": ("extracto.pdf", b"%PDF-1.4 fake content", "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["estado"] in ("completada", "no_completada")
        assert "movimientos_detalle" in data
        assert "advertencias" in data
        assert "resumen" in data
        assert len(data["movimientos_detalle"]) == 1
