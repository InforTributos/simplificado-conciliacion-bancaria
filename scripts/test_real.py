"""Pruebas reales de conciliacion con los PDFs y JSONs de reales-completas.

Usa ASGITransport (sin servidor corriendo) para probar las 3 carpetas:
- Cartagena (2 movimientos)
- Monteria (926 movimientos)
- Valledupar (35 movimientos)

Uso:
    python scripts/test_real.py [Cartagena|Monteria|Valledupar]
    python scripts/test_real.py  # todas
"""

import json
import os
import sys
import time
import asyncio

import httpx



BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
FIXTURES_DIR = os.path.join(BASE_DIR, "tests", "fixtures", "reales-completas")


async def test_folder(folder_name: str):
    """Send a real conciliation test for one folder."""

    json_path = os.path.join(FIXTURES_DIR, folder_name, "movimientos.json")
    pdf_path = os.path.join(FIXTURES_DIR, folder_name, "extracto.pdf")

    if not os.path.exists(json_path):
        print(f"  [SKIP] No existe {json_path}")
        return None
    if not os.path.exists(pdf_path):
        print(f"  [SKIP] No existe {pdf_path}")
        return None

    with open(json_path, encoding="utf-8") as f:
        movimientos = json.load(f)

    movimientos_str = json.dumps(movimientos)
    pdf_bytes = open(pdf_path, "rb").read()

    print(f"  Enviando: {folder_name} ({len(movimientos)} movs, {len(pdf_bytes)} bytes PDF) ...")

    from main import app
    transport = httpx.ASGITransport(app=app)
    start = time.perf_counter()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/conciliaciones/procesar",
            data={
                "movimientos_detalle": movimientos_str,
            },
            files={
                "extracto": (
                    os.path.basename(pdf_path),
                    pdf_bytes,
                    "application/pdf",
                ),
            },
            timeout=120,
        )

    elapsed = time.perf_counter() - start
    return response, elapsed


async def main():
    folders = ["Cartagena", "Monteria", "Valledupar"]

    # Filtro por argumento
    if len(sys.argv) > 1:
        arg = sys.argv[1].capitalize()
        if arg in folders:
            folders = [arg]
        else:
            print(f"Carpeta no encontrada: {arg}. Opciones: {folders}")
            return

    print("=" * 80)
    print("PRUEBAS REALES DE CONCILIACION")
    print("=" * 80)

    for folder in folders:
        print(f"\n--- {folder} ---")
        result = await test_folder(folder)
        if result is None:
            continue

        response, elapsed = result

        if response.status_code != 200:
            print(f"  ERROR HTTP {response.status_code}")
            try:
                detail = response.json()
                print(json.dumps(detail, indent=2, ensure_ascii=False))
            except Exception:
                print(response.text)
            continue

        data = response.json()

        print(f"  Estado       : {data.get('estado', '?')}")
        print(f"  Periodo      : {data.get('periodo', '?')}")
        print(f"  Diferencia   : {data.get('cuadre_diferencia', '?')}")
        r = data.get("resumen", {})
        print(f"  Resumen      : {r.get('total_movimientos',0)} total, "
              f"{r.get('conciliados',0)} conciliados, "
              f"{r.get('no_conciliados',0)} no conciliados "
              f"({r.get('porcentaje_conciliacion',0)}%)")

        m = data.get("metricas", {})
        print(f"  Tiempo API   : {m.get('tiempo_total_ms','?')} ms")

        advertencias = data.get("advertencias", [])
        if advertencias:
            print(f"  Advertencias : {len(advertencias)}")
            for a in advertencias:
                print(f"    - [{a['tipo']}] {a['mensaje']}")

        # Muestra primeros movs conciliados/no conciliados
        movs = data.get("movimientos_detalle", [])
        conciliados = [m for m in movs if m.get("conciliado")]
        no_conciliados = [m for m in movs if not m.get("conciliado")]

        if conciliados:
            print(f"  Conciliados  : {len(conciliados)} (muestra primeros 5):")
            for m in conciliados[:5]:
                print(f"    {m['fecha']} | {m['codigo_movimiento']} | "
                      f"D:{m['debito']} C:{m['credito']} | S:{m['saldo']}")

        if no_conciliados:
            print(f"  No conciliados: {len(no_conciliados)} (muestra primeros 5):")
            for m in no_conciliados[:5]:
                print(f"    {m['fecha']} | {m['codigo_movimiento']} | "
                      f"D:{m['debito']} C:{m['credito']} | S:{m['saldo']}")

    print("\n" + "=" * 80)
    print("FIN")


if __name__ == "__main__":
    asyncio.run(main())
