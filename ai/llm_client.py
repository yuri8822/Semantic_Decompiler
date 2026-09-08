"""
Unified LLM client — thin facade over the per-model implementations in
ai/providers/. Each provider (Anthropic, Xiaomi, Ollama, llama.cpp, DeepSeek) lives in
its own file so it can be tuned independently; this class just picks one
and forwards complete() calls to it.

All providers receive identical system + user prompts, so the rest of the
pipeline is completely provider-agnostic.
"""

from config import LLM_PROVIDER, OLLAMA_MODEL
from ai.providers import get_provider


class LLMClient:
    def __init__(self, provider: str = LLM_PROVIDER, ollama_model: str = OLLAMA_MODEL):
        self.provider = provider.lower()
        self._impl = get_provider(self.provider, ollama_model=ollama_model)

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        """Send a system + user prompt and return the model's text response."""
        return self._impl.complete(system, user, pass_num)
