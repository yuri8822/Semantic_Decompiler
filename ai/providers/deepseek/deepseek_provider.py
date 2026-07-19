"""
DeepSeek — cloud provider, OpenAI-compatible API.
One model for all passes. https://platform.deepseek.com
"""

import os

from config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, MAX_TOKENS
from ai.providers.base import BaseProvider


class DeepSeekProvider(BaseProvider):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        )

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        resp = self._client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
