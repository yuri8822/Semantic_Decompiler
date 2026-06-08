"""
Unified LLM client — routes calls to Anthropic (cloud), Xiaomi MiMo (cloud), or Ollama (local).

Anthropic: uses claude-opus-4-8 for passes 3/4, claude-sonnet-4-6 for all others.
Xiaomi:    uses mimo-v2.5-pro for all passes (Anthropic-compatible API).
Ollama:    uses a single configured model for all passes (no tier split).

All providers receive identical system + user prompts, so the rest of the
pipeline is completely provider-agnostic.
"""

import os

from config import (
    LLM_PROVIDER,
    ANTHROPIC_MODEL_HEAVY,
    ANTHROPIC_MODEL_FAST,
    XIAOMI_BASE_URL,
    XIAOMI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    MAX_TOKENS,
    OLLAMA_MAX_TOKENS,
)

# Passes that benefit from the heavier Anthropic model
_HEAVY_PASSES = {3, 4}


class LLMClient:
    def __init__(self, provider: str = LLM_PROVIDER, ollama_model: str = OLLAMA_MODEL):
        self.provider = provider.lower()
        self.ollama_model = ollama_model

        if self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic()

        elif self.provider == "xiaomi":
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=os.environ.get("XIAOMI_API_KEY", ""),
                base_url=XIAOMI_BASE_URL,
            )

        elif self.provider == "ollama":
            from openai import OpenAI
            self._client = OpenAI(
                base_url=OLLAMA_BASE_URL,
                api_key="ollama",   # required by the openai SDK but ignored by Ollama
            )

        else:
            raise ValueError(
                f"Unknown provider {provider!r}. "
                "Set LLM_PROVIDER to 'anthropic', 'xiaomi', or 'ollama' in config.py."
            )

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        """Send a system + user prompt and return the model's text response."""
        if self.provider == "anthropic":
            return self._anthropic_complete(system, user, pass_num)
        elif self.provider == "xiaomi":
            return self._xiaomi_complete(system, user)
        else:
            return self._ollama_complete(system, user)

    # ------------------------------------------------------------------
    # Provider backends
    # ------------------------------------------------------------------

    def _anthropic_complete(self, system: str, user: str, pass_num: int) -> str:
        model = ANTHROPIC_MODEL_HEAVY if pass_num in _HEAVY_PASSES else ANTHROPIC_MODEL_FAST
        msg = self._client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    def _xiaomi_complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=XIAOMI_MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    def _ollama_complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.ollama_model,
            max_tokens=OLLAMA_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
