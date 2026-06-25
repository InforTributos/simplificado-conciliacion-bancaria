"""Base class for bank statement parsers (Strategy pattern)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from concilia_engine.models import InfoExtracto, MovimientoExtracto

logger = logging.getLogger(__name__)


class BankParser(ABC):
    """Abstract base for all bank-specific parsers.

    Subclasses implement:
    - puede_parsear(): detect if this parser handles the given statement
    - parsear(): extract and normalize movements from statement text
    - _extract_info(): extract metadata (banco, cuenta, periodo, saldos)
    """

    banco_nombre: str = ""
    invertir_lado: str = "contabilidad"  # "contabilidad" | "extracto" | "ninguno"

    @abstractmethod
    def puede_parsear(self, texto: str) -> bool:
        """Return True if this parser can handle the given statement text."""
        ...

    @abstractmethod
    def parsear(self, texto: str) -> list[MovimientoExtracto]:
        """Extract and normalize all movements from statement text."""
        ...

    @abstractmethod
    def extraer_info(self, texto: str) -> InfoExtracto:
        """Extract statement metadata: banco, cuenta, periodo, saldos."""
        ...
