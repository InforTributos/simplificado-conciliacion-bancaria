"""Configuration models for the reconciliation engine."""

from dataclasses import dataclass, field


@dataclass
class MatchConfig:
    max_dias_diferencia: int = 3
    tolerancia_monto: float = 0.00
    max_grupo_items: int = 50
    invertir_naturaleza: bool = True
    forzar_llm: bool = False


@dataclass
class ParseConfig:
    forzar_llm: bool = False
    cuenta_esperada: str | None = None
    max_file_size_mb: int = 50
    use_markitdown: bool = True


@dataclass
class LLMConfig:
    # Primary provider (extraction) — VL model, good JSON handling
    api_key: str = ""
    model: str = "nvidia_nim/nvidia/llama-3.1-nemotron-nano-vl-8b-v1"
    # Orchestrator (format analysis — fast, reliable model)
    orchestrator_model: str = "nvidia_nim/meta/llama-3.1-8b-instruct"
    orchestrator_api_key: str = ""
    # Backup
    backup_api_key: str = ""
    backup_model: str = "nvidia_nim/meta/llama-3.1-8b-instruct"
    # Second backup (disabled by default)
    second_backup_api_key: str = ""
    second_backup_model: str = ""
    # Vision model for scanned/image PDFs
    vision_model: str = "nvidia_nim/nvidia/llama-3.1-nemotron-nano-vl-8b-v1"
    vision_api_key: str = ""
    # Common settings
    max_tokens: int = 4096
    timeout: int = 90
    max_context_chars: int = 80000
    pricing: dict[str, float] = field(default_factory=lambda: {"input": 0.10, "output": 0.40})
