"""
Provider registry — each LLM backend lives in its own file so it can be
tuned independently without touching the others.
"""

from ai.providers.anthropic.anthropic_provider import AnthropicProvider
from ai.providers.xiaomi.xiaomi_provider import XiaomiProvider
from ai.providers.ollama.ollama_provider import OllamaProvider
from ai.providers.bonsai.bonsai_provider import BonsaiProvider

_REGISTRY = {
    "anthropic": AnthropicProvider,
    "xiaomi": XiaomiProvider,
    "ollama": OllamaProvider,
    "bonsai": BonsaiProvider,
}


def get_provider(name: str, ollama_model: str = None):
    key = name.lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown provider {name!r}. "
            f"Set LLM_PROVIDER to one of {sorted(_REGISTRY)} in config.py."
        )
    if key == "ollama":
        return cls(model=ollama_model)
    return cls()
