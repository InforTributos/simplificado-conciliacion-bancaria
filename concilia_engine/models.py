"""Domain models for the reconciliation engine (dataclasses, no DB dependency)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class MovimientoExtracto:
    """Movimiento individual del extracto bancario (lado banco)."""
    id: str  # EXT-NNNN
    fecha: date
    valor: float
    naturaleza: str  # "debito" | "credito"
    descripcion: str
    referencia: Optional[str] = None
    naturaleza_matching: Optional[str] = None  # Set by Nivel 0 (unused for extracto)


@dataclass
class MovimientoContable:
    """Movimiento individual de la contabilidad (lado empresa)."""
    id: str  # CTB-NNNN
    fecha: date
    valor: float
    naturaleza: str  # "debito" | "credito" (original, pre-inversion)
    descripcion: str
    referencia: Optional[str] = None
    tipo_documento: Optional[str] = None  # NCO, CE, IRM, etc.
    codigo_comprobante: Optional[str] = None
    naturaleza_matching: Optional[str] = None  # Set by Nivel 0


@dataclass
class InfoExtracto:
    """Metadatos del extracto bancario: banco, cuenta, periodo y saldos."""
    banco: str
    numero_cuenta: str
    periodo_inicio: date
    periodo_fin: date
    saldo_anterior: float
    saldo_final: float


@dataclass
class InfoContabilidad:
    """Metadatos de la contabilidad: periodo y total de registros."""
    periodo_inicio: date
    periodo_fin: date
    total_registros: int


@dataclass
class Match:
    """Resultado de un match individual entre extracto y contabilidad."""
    nivel: int
    confianza: float
    movimiento_extracto: MovimientoExtracto | list[MovimientoExtracto]
    movimiento_contabilidad: MovimientoContable | list[MovimientoContable]
    tipo: Optional[str] = None  # "exacto", "fecha_flexible", "extracto_uno_contabilidad_muchos", etc.
    dias_diferencia: Optional[int] = None
    multiple_candidates: bool = False


@dataclass
class CuadreFinal:
    """Resultado de la formula de cuadre (nivel 4)."""
    saldo_libros: float
    saldo_extracto: float
    partidas_libros: float
    partidas_extracto: float
    cheques_no_cobrados: float
    consignaciones_transito: float
    suma_iguales_libros: float
    suma_iguales_extracto: float
    diferencia: float


@dataclass
class ResumenConciliacion:
    """Resumen estadistico de la conciliacion."""
    movimientos_extracto: int
    movimientos_contabilidad: int
    conciliados_nivel_1: int = 0
    conciliados_nivel_2: int = 0
    conciliados_nivel_3: int = 0
    total_conciliados: int = 0
    no_conciliados_extracto: int = 0
    no_conciliados_contabilidad: int = 0
    porcentaje_conciliacion: float = 0.0


@dataclass
class ConciliacionResult:
    """Resultado completo del proceso de conciliacion (matches + no conciliados + resumen + cuadre)."""
    matches: list[Match] = field(default_factory=list)
    no_conciliados_extracto: list[MovimientoExtracto] = field(default_factory=list)
    no_conciliados_contabilidad: list[MovimientoContable] = field(default_factory=list)
    resumen: Optional[ResumenConciliacion] = None
    cuadre_final: Optional[CuadreFinal] = None


@dataclass
class ParseResult:
    """Resultado del parsing de un PDF: movimientos extraidos + metadata + errores estructurados."""
    movimientos: list[MovimientoExtracto]
    info_extracto: InfoExtracto
    parser_utilizado: str
    parser_fallback: bool = False
    token_usage: Optional[TokenUsage] = None
    error: dict | None = None  # {"codigo": "ERR_...", "mensaje": "...", "accion": "rechazar|reintentar_vision|..."}


@dataclass
class TokenUsage:
    """Conteo de tokens usados en llamadas a LLM."""
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_used: str
    estimated_cost_usd: float = 0.0


@dataclass
class ParseMetrics:
    """Metricas de rendimiento del proceso de parsing."""
    tiempo_parsing_ms: int = 0
    parser_utilizado: str = ""
    parser_fallback: bool = False
    extraction_rate: float = 0.0
    movimientos_extraidos: int = 0
