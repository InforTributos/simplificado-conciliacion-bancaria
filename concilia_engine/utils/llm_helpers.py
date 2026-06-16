"""Shared LLM response parsing helpers.

Used by both llm.py (legacy fallback) and llm_subagent.py (orchestrator pipeline)
to avoid code duplication when cleaning and parsing JSON from LLM responses.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def clean_and_parse_llm_json(text: str) -> dict | None:
    """Clean and parse JSON from an LLM response.

    Handles:
    - Markdown code fences (``````json ... ```````)
    - Leading language tags (`````` ``json\\n{...}`` ````)
    - JSON embedded in non-JSON text (regex fallback)

    Returns the parsed dict (must contain ``"movimientos"`` key), or
    ``None`` if no valid JSON was found.
    """
    text = text.strip()

    # Step 1: Remove markdown code fences (split-based, handles multi-block)
    if text.startswith("```"):
        parts = text.split("```")
        # parts[0] = "" (the initial ```), parts[1] = content, parts[2..] = rest
        text = parts[1] if len(parts) >= 2 else text
        # Strip language tag (e.g. "json\n" or "json{" -> skip first line/piece)
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Step 2: Try direct JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "movimientos" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Step 3: Regex fallback — extract the first top-level { } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and "movimientos" in data:
                return data
        except json.JSONDecodeError:
            logger.debug("Regex-extracted JSON block is not valid")

    return None
