"""Prueba las 16 carpetas nuevas contra el API /api/v1/conciliaciones/procesar."""

import asyncio
import json
import os
import re
import sys
import time

import httpx
import openpyxl

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "tests", "fixtures", "reales-completas")
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))
from xlsx_to_json import extract_movements

API_URL = "http://localhost:8000/api/v1/conciliaciones/procesar"


def extract_account_number(raw_str):
    if not raw_str:
        return ""
    match = re.search(r'(\d[\d\-]{4,})', raw_str.split('CENTRAL')[-1] if 'CENTRAL' in raw_str else raw_str)
    if match:
        return match.group(1).strip()
    return raw_str.split()[0] if raw_str else ""


def get_xlsx_info(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    account_raw = ""
    for row in ws.iter_rows(min_row=8, max_row=8, values_only=True):
        if row[1]:
            account_raw = str(row[1])
    movements = extract_movements(ws)
    wb.close()
    account_num = extract_account_number(account_raw)
    return account_num, movements


def find_extract_pdf(folder_path):
    pdfs = [f for f in os.listdir(folder_path) if f.lower().endswith('.pdf')]
    ext_pdfs = [p for p in pdfs if p.upper().startswith('EXT')]
    if ext_pdfs:
        return os.path.join(folder_path, ext_pdfs[0])
    if pdfs:
        return os.path.join(folder_path, pdfs[0])
    return None


async def test_folder(folder_name):
    folder_path = os.path.join(FIXTURES_DIR, folder_name)
    if not os.path.isdir(folder_path):
        return {"folder": folder_name, "status": "SKIP", "reason": "no folder"}

    xlsx_files = [f for f in os.listdir(folder_path) if f.endswith('.xlsx')]
    if not xlsx_files:
        return {"folder": folder_name, "status": "SKIP", "reason": "no xlsx"}

    xlsx_path = os.path.join(folder_path, xlsx_files[0])
    account_num, movements = get_xlsx_info(xlsx_path)

    if not movements:
        return {"folder": folder_name, "status": "SKIP", "reason": "0 movements", "account": account_num}

    pdf_path = find_extract_pdf(folder_path)
    if not pdf_path:
        return {"folder": folder_name, "status": "SKIP", "reason": "no pdf"}

    pdf_bytes = open(pdf_path, "rb").read()
    movs_json = json.dumps(movements, ensure_ascii=False)

    files = {"extracto": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")}
    data = {"movimientos_detalle": movs_json, "periodo": "202603"}
    if account_num:
        data["cuenta_bancaria"] = json.dumps({"numero_cuenta_bancaria": account_num})

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            t0 = time.time()
            resp = await client.post(API_URL, files=files, data=data)
            elapsed = time.time() - t0

        if resp.status_code == 200:
            result = resp.json()
            return {
                "folder": folder_name, "status": "OK",
                "estado": result.get("estado"),
                "diferencia": result.get("cuadre_diferencia"),
                "total": result.get("resumen", {}).get("total_movimientos"),
                "conciliados": result.get("resumen", {}).get("conciliados"),
                "pct": result.get("resumen", {}).get("porcentaje_conciliacion"),
                "account": account_num,
                "movs_json": len(movements),
                "elapsed": f"{elapsed:.1f}s",
                "warnings": result.get("advertencias", []),
            }
        else:
            return {
                "folder": folder_name, "status": f"HTTP {resp.status_code}",
                "detail": resp.text[:300], "account": account_num, "movs_json": len(movements),
            }
    except Exception as e:
        return {"folder": folder_name, "status": "ERROR", "detail": str(e)[:300]}


async def main():
    all_folders = sorted([
        f for f in os.listdir(FIXTURES_DIR)
        if f not in ("Cartagena", "Monteria", "Valledupar")
        and os.path.isdir(os.path.join(FIXTURES_DIR, f))
    ])

    folders_to_test = []
    for folder in all_folders:
        json_path = os.path.join(FIXTURES_DIR, folder, "movimientos.json")
        if not os.path.exists(json_path):
            folders_to_test.append(folder)

    if not folders_to_test:
        print("Todas las carpetas ya tienen movimientos.json. Probando todas...")
        folders_to_test = all_folders

    print(f"Probando {len(folders_to_test)} carpetas...")
    print()

    results = []
    for folder in folders_to_test:
        result = await test_folder(folder)
        results.append(result)
        status = result["status"]
        if status == "OK":
            dif = result.get("diferencia", "?")
            pct = result.get("pct", "?")
            conc = result.get("conciliados", "?")
            total = result.get("total", "?")
            print(f"  {result['folder']:25s} | {result['estado']:15s} | dif={str(dif):>15} | {conc}/{total} ({pct}%) | {result['elapsed']}")
        elif status.startswith("HTTP"):
            print(f"  {result['folder']:25s} | {status} | {result.get('detail', '')[:100]}")
        else:
            print(f"  {result['folder']:25s} | {status} | {result.get('reason', result.get('detail', ''))[:100]}")

    print()
    print("=" * 100)
    print("RESUMEN")
    print("=" * 100)
    ok = [r for r in results if r["status"] == "OK"]
    errs = [r for r in results if r["status"] not in ("OK",) and "SKIP" not in r["status"]]
    skips = [r for r in results if "SKIP" in r["status"]]
    print(f"  OK:       {len(ok)}/{len(results)}")
    print(f"  Errores:  {len(errs)}")
    print(f"  Saltados: {len(skips)}")
    if ok:
        completada = [r for r in ok if r["estado"] == "completada"]
        no_completada = [r for r in ok if r["estado"] != "completada"]
        print(f"\n  completada:     {len(completada)}/{len(ok)}")
        print(f"  no_completada:  {len(no_completada)}/{len(ok)}")
        if no_completada:
            print("\n  NO COMPLETADAS:")
            for r in no_completada:
                print(f"    {r['folder']:25s} | dif={r.get('diferencia', '?')} | {r.get('conciliados','?')}/{r.get('total','?')} ({r.get('pct','?')}%)")
    if errs:
        print("\n  ERRORES:")
        for r in errs:
            print(f"    {r['folder']:25s} | {r['status']} | {r.get('detail', '')[:100]}")
    if skips:
        print("\n  SALTADOS:")
        for r in skips:
            print(f"    {r['folder']:25s} | {r.get('reason', '')}")


if __name__ == "__main__":
    asyncio.run(main())
