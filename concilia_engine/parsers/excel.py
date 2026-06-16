"""Parser for accounting files (XLSX/XLS/CSV) from TREASURY ERP and others."""

from __future__ import annotations

import io
import logging
from typing import Optional

import pandas as pd

from concilia_engine.models import MovimientoContable
from concilia_engine.normalizer import normalize_description, parse_date

logger = logging.getLogger(__name__)

# Keywords for header detection (case-insensitive)
HEADER_KEYWORDS = {
    "fecha": ["FECHA", "DATE", "FEC"],
    "debito": ["DEBITO", "DEBE", "DEBITOS", "DEBIT"],
    "credito": ["CREDITO", "HABER", "CREDITOS", "CREDIT"],
    "descripcion": ["CONCEPTO", "DESCRIPCION", "DETALLE", "GLOSA", "OBSERVACION"],
    "tipo_documento": ["TIPO", "TIPO_DOC", "TD", "TIPO DOCUMENTO"],
    "comprobante": ["COMPROBANTE", "NUMERO", "NUM", "NRO", "NO_COMPROBANTE"],
    "referencia": ["REFERENCIA", "DOCUMENTO", "DOC", "REF"],
    "tercero": ["TERCERO", "NIT_TERCERO", "ID_TERCERO", "NOMBRE_TERCERO"],
    "centro_costo": ["CENTRO", "CENTRO_COSTO", "CC"],
}

# Known document types (extensible)
KNOWN_TIPOS_DOCUMENTO = {
    "NCO", "CE", "IRM", "NCI", "NDB", "NCR", "ING", "REC",
    "FAC", "CXP", "CXC", "AJU", "TRS", "DEP",
}


class ExcelParser:
    """Parse accounting files with variable column layouts.

    Handles:
    - 11-column layout (Monteria): no Centro Costo
    - 12-column layout (Valledupar): with Centro Costo
    - 13-column layout (Cartagena): with ID_Tercero + Nombre_Tercero separate
    """

    def parse_contabilidad(
        self,
        file_bytes: bytes,
        filename: str,
    ) -> list[MovimientoContable]:
        """Main entry: load file -> find header -> map columns -> parse rows."""
        df = self._load_file(file_bytes, filename)
        if df is None or df.empty:
            raise ValueError(f"No se pudo leer el archivo: {filename}")

        header_row = self._detect_header_row(df)
        if header_row is None:
            raise ValueError("No se encontro fila de encabezados en el archivo contable")

        logger.info("Header detected at row %d", header_row)

        # Set header and get data rows
        headers = [str(h).strip().upper() for h in df.iloc[header_row].tolist()]
        data_df = df.iloc[header_row + 1:].reset_index(drop=True)
        data_df.columns = range(len(data_df.columns))

        # Map columns by keyword matching
        col_map = self._map_columns(headers)
        logger.info("Column mapping: %s", col_map)

        # Parse rows
        movimientos = []
        seq = 1

        for _, row in data_df.iterrows():
            mov = self._parse_row(row, col_map, seq)
            if mov:
                movimientos.append(mov)
                seq += 1

        logger.info("Excel parser: extracted %d accounting movements from %s", len(movimientos), filename)
        return movimientos

    def _load_file(self, file_bytes: bytes, filename: str) -> pd.DataFrame | None:
        """Load file based on extension."""
        fname_lower = filename.lower()
        try:
            if fname_lower.endswith(".csv"):
                # Try multiple encodings
                for encoding in ("utf-8", "latin-1", "cp1252"):
                    try:
                        return pd.read_csv(
                            io.BytesIO(file_bytes),
                            header=None,
                            encoding=encoding,
                            dtype=str,
                        )
                    except (UnicodeDecodeError, ValueError):
                        continue
            elif fname_lower.endswith(".xls") and not fname_lower.endswith(".xlsx"):
                return pd.read_excel(
                    io.BytesIO(file_bytes),
                    header=None,
                    dtype=str,
                    engine="xlrd",
                )
            else:  # .xlsx
                return pd.read_excel(
                    io.BytesIO(file_bytes),
                    header=None,
                    dtype=str,
                    engine="openpyxl",
                )
        except Exception as e:
            logger.error("Failed to load file %s: %s", filename, str(e))
            return None

    def _detect_header_row(self, df: pd.DataFrame) -> int | None:
        """Scan rows 0-20 for keyword matches. Return row with highest score."""
        best_row = None
        best_score = 0

        scan_limit = min(20, len(df))
        for row_idx in range(scan_limit):
            row_values = [str(v).strip().upper() for v in df.iloc[row_idx].tolist() if pd.notna(v)]
            score = 0
            for cell in row_values:
                # Check if cell matches any keyword
                for _field, keywords in HEADER_KEYWORDS.items():
                    for kw in keywords:
                        if kw in cell:
                            score += 1
                            break

            if score > best_score:
                best_score = score
                best_row = row_idx

        # Require at least 3 keyword matches (fecha + debito + credito minimum)
        if best_score >= 3:
            return best_row
        return None

    def _map_columns(self, headers: list[str]) -> dict[str, int | None]:
        """Map detected headers to normalized field names by keyword matching."""
        col_map: dict[str, int | None] = {
            "fecha": None,
            "debito": None,
            "credito": None,
            "descripcion": None,
            "tipo_documento": None,
            "comprobante": None,
            "referencia": None,
        }

        for col_idx, header in enumerate(headers):
            if not header or header == "NAN":
                continue

            for field, keywords in HEADER_KEYWORDS.items():
                if field in ("tercero", "centro_costo"):
                    continue  # Ignored columns (RF-14d)

                if field not in col_map:
                    continue

                for kw in keywords:
                    if kw in header and col_map.get(field) is None:
                        col_map[field] = col_idx
                        break

        return col_map

    def _parse_row(
        self,
        row: pd.Series,
        col_map: dict[str, int | None],
        seq: int,
    ) -> MovimientoContable | None:
        """Parse a single data row into a MovimientoContable."""
        # Fecha (required)
        fecha_col = col_map.get("fecha")
        if fecha_col is None:
            return None

        fecha_raw = str(row.iloc[fecha_col]) if fecha_col < len(row) else ""
        if not fecha_raw or fecha_raw == "nan":
            return None

        fecha = parse_date(fecha_raw)
        if not fecha:
            return None

        # Debito / Credito
        debito_col = col_map.get("debito")
        credito_col = col_map.get("credito")

        debito_val = self._parse_cell_amount(row, debito_col)
        credito_val = self._parse_cell_amount(row, credito_col)

        if debito_val and debito_val > 0:
            valor = debito_val
            naturaleza = "debito"
        elif credito_val and credito_val > 0:
            valor = credito_val
            naturaleza = "credito"
        else:
            return None  # Skip rows with no amounts

        # Descripcion
        desc_col = col_map.get("descripcion")
        descripcion = ""
        if desc_col is not None and desc_col < len(row):
            raw = str(row.iloc[desc_col])
            if raw != "nan":
                descripcion = normalize_description(raw)

        # Tipo documento
        tipo_doc = None
        td_col = col_map.get("tipo_documento")
        if td_col is not None and td_col < len(row):
            raw = str(row.iloc[td_col]).strip().upper()
            if raw != "NAN" and raw:
                tipo_doc = raw

        # Comprobante
        comprobante = None
        comp_col = col_map.get("comprobante")
        if comp_col is not None and comp_col < len(row):
            raw = str(row.iloc[comp_col]).strip()
            if raw != "nan" and raw:
                comprobante = raw

        # Referencia
        referencia = None
        ref_col = col_map.get("referencia")
        if ref_col is not None and ref_col < len(row):
            raw = str(row.iloc[ref_col]).strip()
            if raw != "nan" and raw:
                referencia = raw

        return MovimientoContable(
            id=f"CTB-{seq:04d}",
            fecha=fecha,
            valor=abs(valor),
            naturaleza=naturaleza,
            descripcion=descripcion,
            referencia=referencia,
            tipo_documento=tipo_doc,
            codigo_comprobante=comprobante,
        )

    def _parse_cell_amount(self, row: pd.Series, col_idx: int | None) -> float | None:
        """Parse amount from a cell, handling various formats."""
        if col_idx is None or col_idx >= len(row):
            return None

        raw = str(row.iloc[col_idx]).strip()
        if not raw or raw == "nan" or raw == "0" or raw == "0.0":
            return None

        # Remove whitespace and currency symbols
        raw = raw.replace(" ", "").replace("$", "")

        # Detect format
        if "," in raw and "." in raw:
            # Has both separators — detect which is decimal
            if raw.rfind(",") > raw.rfind("."):
                # CO format: 1.234,56
                raw = raw.replace(".", "").replace(",", ".")
            else:
                # US format: 1,234.56
                raw = raw.replace(",", "")
        elif "," in raw:
            # Only comma: could be CO decimal or US thousands
            # If exactly 2 digits after comma, treat as decimal
            parts = raw.split(",")
            if len(parts) == 2 and len(parts[1]) == 2:
                raw = raw.replace(",", ".")
            else:
                raw = raw.replace(",", "")
        # If only dot: already in correct format

        try:
            val = float(raw)
            return val if val != 0 else None
        except ValueError:
            return None
