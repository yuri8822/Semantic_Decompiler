"""
Ollama — local provider, via its OpenAI-compatible endpoint.
One configured model for all passes (no tier split). https://ollama.com/library
"""

from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_MAX_TOKENS
from ai.providers.base import BaseProvider


class OllamaProvider(BaseProvider):
    def __init__(self, model: str = None):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",   # required by the openai SDK but ignored by Ollama
        )
        self._model = model or OLLAMA_MODEL

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            max_tokens=OLLAMA_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
