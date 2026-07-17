"""
Bonsai 27B (1-bit) — local provider, via llama.cpp's OpenAI-compatible
server. Requires PrismML's llama.cpp fork (the Q1_0_g128 hybrid-attention
kernels aren't in vanilla llama.cpp): https://github.com/PrismML-Eng/llama.cpp

The fork is vendored at ai/providers/bonsai/llama.cpp/ (gitignored — it's a
built local dependency, not project source). Prebuilt Windows CUDA binaries
live at ai/providers/bonsai/llama.cpp/bin/extracted/ (no compilation needed;
MSVC isn't required since these ship prebuilt).

Model weights: https://huggingface.co/prism-ml/Bonsai-27B-gguf
(Bonsai-27B-Q1_0.gguf — this is the true 1-bit repo, not the
Ternary-Bonsai-27B-gguf variant). Lives at ai/providers/bonsai/model/
(gitignored — *.gguf files aren't committed).

Start the server from ai/providers/bonsai/llama.cpp/bin/extracted/ (three
levels up from there back to ai/providers/bonsai/, then into model/):
    ./llama-server.exe -m ../../../model/Bonsai-27B-Q1_0.gguf --host 0.0.0.0 --port 8080 -ngl 99

Verified against a live server (2026-07-17): thinking is on by default (Qwen3-
style chat template) and the reasoning trace arrives in a separate
`message.reasoning_content` field, NOT inline `<think>` tags in `content`.
Left unbounded, this is dangerous — a run can burn the entire `max_tokens`
budget on the reasoning trace and return `content=""` with
`finish_reason="length"`, which would silently feed an empty string into the
translator as "the translated code".

Originally worked around by suppressing thinking entirely (client-side
`extra_body={"chat_template_kwargs": {"enable_thinking": False}}`). Since
then, decided the reasoning itself is worth keeping — it's just the
*unbounded* length that's the problem.

First attempt at a per-request budget used a `"reasoning_budget"` field
(matching the server's `--reasoning-budget` CLI flag name) — confirmed
empirically ignored. That was simply the wrong field name, not a real
protocol limitation: reading llama.cpp's own server-common.cpp showed the
actual per-request field is `thinking_budget_tokens`, and — critically —
it's ONLY consulted when the server itself was NOT started with
`--reasoning-budget` (that flag wins whenever set; the per-request field is
just the fallback). So the fix that actually unlocked per-pass control was
removing `--reasoning-budget` from start_bonsai.bat entirely (back to the
server's own unrestricted default) and sending `thinking_budget_tokens` here
instead, varied per pass — mirroring ANTHROPIC_MODEL_HEAVY/_FAST's existing
pass 3/4 split (type inference, class reconstruction get more budget than
the more mechanical passes).

The `<think>...</think>` regex stays as a defense-in-depth no-op in case a
future server config re-enables inline-tag reasoning instead
(`--reasoning-format deepseek-legacy` or `none`), and the empty-content
check below stays as a backstop in case a pass's budget is ever set too low
and reasoning eats the whole response again.
"""

import re

from config import (
    BONSAI_BASE_URL, BONSAI_MODEL, BONSAI_MAX_TOKENS,
    BONSAI_REASONING_BUDGET_HEAVY, BONSAI_REASONING_BUDGET_FAST,
)
from ai.providers.base import BaseProvider

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_HEAVY_PASSES = {3, 4}  # type inference, class reconstruction — same split as AnthropicProvider


class BonsaiProvider(BaseProvider):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=BONSAI_BASE_URL,
            api_key="bonsai",   # required by the openai SDK, ignored by llama.cpp's server
        )

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        budget = BONSAI_REASONING_BUDGET_HEAVY if pass_num in _HEAVY_PASSES else BONSAI_REASONING_BUDGET_FAST
        resp = self._client.chat.completions.create(
            model=BONSAI_MODEL,
            max_tokens=BONSAI_MAX_TOKENS,
            extra_body={"thinking_budget_tokens": budget},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        if not text and getattr(msg, "reasoning_content", None):
            raise RuntimeError(
                "Bonsai returned only a reasoning trace and no content "
                f"(finish_reason={resp.choices[0].finish_reason!r}). "
                f"The reasoning trace (budget {budget}) likely ate the whole "
                "max_tokens budget — make sure start_bonsai.bat is NOT passing "
                "--reasoning-budget (it must stay unset for thinking_budget_tokens "
                "to take effect), or raise BONSAI_MAX_TOKENS in config.py."
            )
        return _THINK_BLOCK.sub("", text).strip()
