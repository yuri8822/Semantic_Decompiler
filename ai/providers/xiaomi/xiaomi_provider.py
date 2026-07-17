"""
Xiaomi MiMo — cloud provider, Anthropic-compatible API.
One model for all passes. https://platform.xiaomimomo.com
"""

import os

from config import XIAOMI_BASE_URL, XIAOMI_MODEL, MAX_TOKENS
from ai.providers.base import BaseProvider


class XiaomiProvider(BaseProvider):
    def __init__(self):
        import anthropic
        self._client = anthropic.Anthropic(
            api_key=os.environ.get("XIAOMI_API_KEY", ""),
            base_url=XIAOMI_BASE_URL,
        )

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        msg = self._client.messages.create(
            model=XIAOMI_MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""
