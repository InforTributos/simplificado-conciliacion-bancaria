"""Test all 19 accounts from reales-completas against the running API."""

import json
import os
import time
import httpx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE_DIR, "tests", "fixtures", "reales-completas")
API_URL = "http://localhost:8000/api/v1/conciliaciones/procesar"


def find_pdf(folder_path):
    """Find the extract PDF in a folder (prefer files starting with EXT or extracto)."""
    pdfs = [f for f in os.listdir(folder_path) if f.lower().endswith(".pdf")]
    ext_pdfs = [p for p in pdfs if p.upper().startswith("EXT") or p.upper().startswith("EXTRACTO")]
    if ext_pdfs:
        return os.path.join(folder_path, ext_pdfs[0])
    if pdfs:
        return os.path.join(folder_path, pdfs[0])
    return None


def test_folder(folder_name):
    """Test one folder against the API."""
    folder_path = os.path.join(FIXTURES, folder_name)
    json_path = os.path.join(folder_path, "movimientos.json")
    pdf_path = find_pdf(folder_path)

    if not os.path.exists(json_path):
        return {"folder": folder_name, "status": "SKIP", "reason": "no movimientos.json"}

    with open(json_path, encoding="utf-8") as f:
        movimientos = json.load(f)

    if not movimientos:
        return {"folder": folder_name, "status": "SKIP", "reason": "0 movimientos"}

    if not pdf_path:
        return {"folder": folder_name, "status": "SKIP", "reason": "no PDF"}

    pdf_bytes = open(pdf_path, "rb").read()
    movs_json = json.dumps(movimientos, ensure_ascii=False)

    files = {"extracto": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")}
    # Cartagena/Monteria/Valledupar are not March 2026 — let API auto-detect period
    if folder_name in ("Cartagena", "Monteria", "Valledupar"):
        data = {"movimientos_detalle": movs_json}
    else:
        data = {"movimientos_detalle": movs_json, "periodo": "202603"}

    try:
        t0 = time.time()
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(API_URL, files=files, data=data)
        elapsed = time.time() - t0

        if resp.status_code == 200:
            r = resp.json()
            resumen = r.get("resumen", {})
            return {
                "folder": folder_name,
                "status": "OK",
                "estado": r.get("estado"),
                "diferencia": r.get("cuadre_diferencia"),
                "total": resumen.get("total_movimientos"),
                "conciliados": resumen.get("conciliados"),
                "no_conciliados": resumen.get("no_conciliados"),
                "pct": resumen.get("porcentaje_conciliacion"),
                "movs_json": len(movimientos),
                "elapsed": f"{elapsed:.1f}s",
                "advertencias": len(r.get("advertencias", [])),
            }
        else:
            return {
                "folder": folder_name,
                "status": f"HTTP {resp.status_code}",
                "detail": resp.text[:200],
                "movs_json": len(movimientos),
            }
    except Exception as e:
        return {"folder": folder_name, "status": "ERROR", "detail": str(e)[:200]}


def main():
    folders = sorted([
        f for f in os.listdir(FIXTURES)
        if os.path.isdir(os.path.join(FIXTURES, f))
    ])

    print(f"Probando {len(folders)} carpetas contra {API_URL}")
    print("=" * 120)

    results = []
    for folder in folders:
        result = test_folder(folder)
        results.append(result)

        if result["status"] == "OK":
            e = result["estado"]
            d = result["diferencia"]
            c = result["conciliados"]
            t = result["total"]
            nc = result["no_conciliados"]
            p = result["pct"]
            mj = result["movs_json"]
            el = result["elapsed"]
            adv = result["advertencias"]
            print(f"  {result['folder']:25s} | {e:15s} | diff={str(d):>12} | {c:>4}/{t:<4} ({p:>6}%) | JSON={mj:>4} | {el} | adv={adv}")
        elif result["status"] == "SKIP":
            print(f"  {result['folder']:25s} | SKIP | {result['reason']}")
        else:
            print(f"  {result['folder']:25s} | {result['status']} | {result.get('detail', '')[:80]}")

    # Summary
    ok = [r for r in results if r["status"] == "OK"]
    skips = [r for r in results if r["status"] == "SKIP"]
    errors = [r for r in results if r["status"] not in ("OK",) and "SKIP" not in r["status"]]

    print()
    print("=" * 120)
    print("RESUMEN")
    print("=" * 120)
    print(f"  OK:       {len(ok)}/{len(results)}")
    print(f"  Errores:  {len(errors)}")
    print(f"  Saltados: {len(skips)}")

    if ok:
        completada = [r for r in ok if r["estado"] == "completada"]
        no_completada = [r for r in ok if r["estado"] != "completada"]
        print(f"\n  completada:     {len(completada)}/{len(ok)}")
        print(f"  no_completada:  {len(no_completada)}/{len(ok)}")

        if completada:
            print("\n  COMPLETADAS:")
            for r in completada:
                print(f"    {r['folder']:25s} | diff={r['diferencia']:>12} | {r['conciliados']}/{r['total']} ({r['pct']}%)")

        if no_completada:
            print("\n  NO COMPLETADAS:")
            for r in no_completada:
                print(f"    {r['folder']:25s} | diff={r['diferencia']:>12} | {r['conciliados']}/{r['total']} ({r['pct']}%)")

    if errors:
        print("\n  ERRORES:")
        for r in errors:
            print(f"    {r['folder']:25s} | {r['status']} | {r.get('detail', '')[:100]}")

    if skips:
        print("\n  SALTADOS:")
        for r in skips:
            print(f"    {r['folder']:25s} | {r.get('reason', '')}")


if __name__ == "__main__":
    main()
