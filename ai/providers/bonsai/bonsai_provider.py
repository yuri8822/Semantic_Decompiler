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
Left unhandled, this is dangerous — a run can burn the entire `max_tokens`
budget on the reasoning trace and return `content=""` with
`finish_reason="length"`, which would silently feed an empty string into the
translator as "the translated code".

Fixed by passing `extra_body={"chat_template_kwargs": {"enable_thinking":
False}}` on every request, which suppresses reasoning entirely — confirmed
this returns the real answer directly in `content` with `finish_reason=
"stop"`. The `<think>...</think>` regex stays as a defense-in-depth no-op in
case a future server config re-enables inline-tag reasoning instead
(`--reasoning-format deepseek-legacy` or `none`), and the empty-content check
below catches the case where reasoning leaks through some other path.
"""

import re

from config import BONSAI_BASE_URL, BONSAI_MODEL, BONSAI_MAX_TOKENS
from ai.providers.base import BaseProvider

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class BonsaiProvider(BaseProvider):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            base_url=BONSAI_BASE_URL,
            api_key="bonsai",   # required by the openai SDK, ignored by llama.cpp's server
        )

    def complete(self, system: str, user: str, pass_num: int = 1) -> str:
        resp = self._client.chat.completions.create(
            model=BONSAI_MODEL,
            max_tokens=BONSAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        if not text and getattr(msg, "reasoning_content", None):
            raise RuntimeError(
                "Bonsai returned only a reasoning trace and no content "
                f"(finish_reason={resp.choices[0].finish_reason!r}). "
                "Reasoning suppression may have failed to apply — check the "
                "server's chat template supports 'enable_thinking', or raise "
                "BONSAI_MAX_TOKENS in config.py."
            )
        return _THINK_BLOCK.sub("", text).strip()
