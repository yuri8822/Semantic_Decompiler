"""
llama.cpp — local provider, via llama-server's OpenAI-compatible endpoint
on http://localhost:8080. Model-agnostic: start llama-server with whatever
GGUF you want and this talks to it as-is.
"""

from config import LLAMACPP_BASE_URL, LLAMACPP_MODEL, LLAMACPP_MAX_TOKENS
from ai.providers.base import BaseProvider


class LlamaCppProvider(BaseProvider):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=LLAMACPP_BASE_URL,
            api_key="llamacpp",   # required by the openai SDK but ignored by llama-server
        )

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        resp = self._client.chat.completions.create(
            model=LLAMACPP_MODEL,
            max_tokens=LLAMACPP_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
