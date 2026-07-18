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

Thinking is on by default (Qwen3-style chat template) and is suppressed here
via `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`. This
was tried the other way — reasoning on, bounded via a per-pass
`thinking_budget_tokens` request field — across a real Chess.exe run
(2026-07-18) and reverted after three distinct failure patterns surfaced in
under an hour, each needing its own reactive fix with no guarantee a fourth
pattern wasn't waiting:
  1. Reasoning narrated as plain text with a dangling `</think>` and no
     opening tag — 13KB of narration silently stored as "clean" output.
  2. A genuinely empty response: the model stopping on its own after a
     short, incomplete reasoning burst, producing no answer at all.
  3. Reasoning narrated as plain text with NO tag at all (opening or
     closing) — no delimiter to even attempt stripping against.
With zero measured translation-quality benefit from reasoning to weigh
against that fragility, suppressing it outright is the reliable choice —
it's what every prior successful run (SpeedRunners, Chess pre-2026-07-18)
used.

The `<think>...</think>` regex below stays as a defense-in-depth no-op in
case a future server/template config re-enables inline-tag reasoning
despite the suppression, and the empty-content check stays as a backstop
in case suppression itself ever fails to apply.
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
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
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
                "Thinking suppression may have failed to apply — check "
                "BONSAI_MAX_TOKENS in config.py."
            )
        return _THINK_BLOCK.sub("", text).strip()
