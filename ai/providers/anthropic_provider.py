"""
Anthropic Claude — cloud provider.

Uses a heavier model for the type-inference / class-reconstruction
passes (3, 4) and a faster model for the rest.
"""

from config import ANTHROPIC_MODEL_HEAVY, ANTHROPIC_MODEL_FAST, MAX_TOKENS
from ai.providers.base import BaseProvider

_HEAVY_PASSES = {3, 4}


class AnthropicProvider(BaseProvider):
    def __init__(self):
        import anthropic
        self._client = anthropic.Anthropic()

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        model = ANTHROPIC_MODEL_HEAVY if pass_num in _HEAVY_PASSES else ANTHROPIC_MODEL_FAST
        msg = self._client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""
