"""MarkItDown converter wrapper — optional PDF-to-markdown preprocessing.

Used to reduce token usage in LLM parser calls and improve text structure
for the generic parser fallback.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markitdown import MarkItDown

logger = logging.getLogger(__name__)


class MarkitdownConverter:
    """Thin wrapper around Microsoft MarkItDown for PDF-to-markdown conversion.

    This class is designed to be used as a singleton — reuse the instance
    via :func:`get_converter` to avoid reloading the ONNX model on every call.
    """

    def __init__(self) -> None:
        self._md: MarkItDown | None = None

    def _ensure_md(self) -> MarkItDown | None:
        if self._md is None:
            try:
                from markitdown import MarkItDown

                self._md = MarkItDown()
            except ImportError:
                logger.warning("markitdown not installed, falling back to raw text")
                return None
        return self._md

    def convert(self, file_bytes: bytes, filename: str = "document.pdf") -> str | None:
        """Convert a PDF (or other document) to markdown text.

        Args:
            file_bytes: Raw file content as bytes.
            filename: Original filename (used for suffix detection).

        Returns:
            Markdown text or ``None`` if conversion failed.
        """
        md = self._ensure_md()
        if md is None:
            return None

        suffix = os.path.splitext(filename)[1] or ".pdf"

        fd = -1
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            fd = -1

            with open(tmp_path, "wb") as f:
                f.write(file_bytes)

            result = md.convert(tmp_path)
            text = result.text_content
            logger.info(
                "MarkItDown: converted %s (%d bytes -> %d chars)",
                filename,
                len(file_bytes),
                len(text),
            )
            return text
        except Exception as e:
            logger.warning("MarkItDown conversion failed for %s: %s", filename, e)
            return None
        finally:
            if fd != -1:
                os.close(fd)
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


_converter: MarkitdownConverter | None = None


def get_converter() -> MarkitdownConverter | None:
    """Return the module-level :class:`MarkitdownConverter` singleton.

    Reusing a single instance avoids re-loading the underlying ONNX model
    (used by ``magika`` for file-type detection) on every PDF conversion.
    """
    global _converter
    if _converter is None:
        _converter = MarkitdownConverter()
    return _converter
