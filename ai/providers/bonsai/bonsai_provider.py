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
Ternary-Bonsai-27B-gguf variant).

Start the server from ai/providers/bonsai/llama.cpp/bin/extracted/:
    ./llama-server.exe -m <path-to>/Bonsai-27B-Q1_0.gguf --host 0.0.0.0 --port 8080 -ngl 99

PrismML's docs describe the 27B variant as served with "thinking"
enabled by default, so a response may be prefixed with a
<think>...</think> reasoning block. That block is stripped here since
the multi-pass translator expects only C++ back — verify against the
server's actual output shape once it's running; if reasoning instead
arrives in a separate `reasoning_content` field this stripping is a
harmless no-op.
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
        )
        text = resp.choices[0].message.content or ""
        return _THINK_BLOCK.sub("", text).strip()
