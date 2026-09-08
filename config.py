from pathlib import Path

# --- Ghidra ---
GHIDRA_PATH = r"D:\Programs\ghidra\support\analyzeHeadless.bat"
GHIDRA_PROJECT_DIR = "ghidra_project"
GHIDRA_PROJECT_NAME = "AIRecon"
GHIDRA_SCRIPT_DIR = "ghidra_scripts"

# --- Paths ---
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
GHIDRA_JSON_DIR = DATA_DIR / "ghidra_json"
BINARIES_DIR = DATA_DIR / "binaries"
OUTPUT_DIR = PROJECT_ROOT / "output" / "recovered"

# --- Database ---
DB_PATH = str(PROJECT_ROOT / "semantic.db")

# --- AI provider ---
# Set LLM_PROVIDER to "anthropic", "xiaomi", "ollama", "llamacpp", or "deepseek"
LLM_PROVIDER = "anthropic"

# Anthropic (cloud)
ANTHROPIC_MODEL_HEAVY = "claude-opus-4-8"    # the single reconstruction call (ai/translator.py always requests the heavy tier)
ANTHROPIC_MODEL_FAST  = "claude-sonnet-4-6"  # the lightweight one-line summary call

# Xiaomi MiMo — https://platform.xiaomimomo.com
XIAOMI_BASE_URL = "https://api.xiaomimimo.com/anthropic/"
XIAOMI_MODEL    = "mimo-v2.5-pro"

# DeepSeek (cloud), OpenAI-compatible API — https://platform.deepseek.com
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL    = "deepseek-v4-pro"

# Ollama (local) — https://ollama.com/library
# Good code-focused models: qwen2.5-coder:7b, deepseek-coder-v2:16b, codellama:13b
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL    = "carstenuhlig/omnicoder-9b:q4_k_m"

# llama.cpp (local) — llama-server's OpenAI-compatible endpoint on port 8080.
# Model-agnostic: start llama-server (a system install, not vendored here)
# with whatever GGUF you want — see start_llamacpp.bat — and the provider
# talks to it as-is.
LLAMACPP_BASE_URL   = "http://localhost:8080/v1"
LLAMACPP_MODEL      = "local"  # sent in the request; llama-server ignores it and serves whatever's loaded
LLAMACPP_MAX_TOKENS = 4096

MAX_TOKENS        = 16384  # Anthropic / Xiaomi / DeepSeek -- 8192 was
# observed truncating real functions mid-statement (Rook::Move,
# Engine::ProcessPlayerMove in a real deepseek run), which then got
# silently accepted as "the whole function". Billed on tokens actually
# generated, not this cap, so raising it doesn't cost anything unless a
# function genuinely needs the extra room -- which some already did.
OLLAMA_MAX_TOKENS = 4096  # local models typically cap at 4k
AI_TIMEOUT_SECONDS = 120
DECOMPILER_TIMEOUT_SECONDS = 60

# --- Output ---
# ProjectWriter.write() fully re-serializes every accumulated function each
# call, so calling it after literally every function is O(n^2) over a large
# run. Throttle it instead: write at most every N functions, or every S
# seconds, whichever comes first. The final write after the loop finishes is
# always unconditional, so nothing is ever missing from disk at the end.
OUTPUT_WRITE_EVERY_N_FUNCTIONS = 5
OUTPUT_WRITE_EVERY_SECONDS = 30

# --- Reviewer loop ---
# After the refactor agent produces a version, a separate reviewer agent
# checks it against the original Ghidra decompiled code (ground truth) and
# either passes it or returns specific issues, which get fed back into
# another refactor attempt. Bounded so a stubborn disagreement can't loop
# forever -- MAX_REVIEW_ROUNDS is the number of review checks performed; if
# the last one still fails, whatever the last attempt produced ships anyway,
# flagged 'unresolved' in the DB rather than blocking the run over one function.
# Set to 0 to disable the review loop entirely -- no reviewer call at all,
# the refactor agent's own output ships as-is (flagged 'skipped' in the DB).
MAX_REVIEW_ROUNDS = 3
