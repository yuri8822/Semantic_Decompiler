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
# Set LLM_PROVIDER to "anthropic", "xiaomi", "ollama", or "bonsai"
LLM_PROVIDER = "anthropic"

# Anthropic (cloud)
ANTHROPIC_MODEL_HEAVY = "claude-opus-4-8"    # passes 3, 4 — type inference, class reconstruction
ANTHROPIC_MODEL_FAST  = "claude-sonnet-4-6"  # passes 1, 2, 5, 6

# Xiaomi MiMo — https://platform.xiaomimomo.com
XIAOMI_BASE_URL = "https://api.xiaomimimo.com/anthropic/"
XIAOMI_MODEL    = "mimo-v2.5-pro"

# Ollama (local) — https://ollama.com/library
# Good code-focused models: qwen2.5-coder:7b, deepseek-coder-v2:16b, codellama:13b
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL    = "carstenuhlig/omnicoder-9b:q4_k_m"

# Bonsai 27B (1-bit, local) — llama.cpp server via PrismML's fork, vendored at
# ai/providers/bonsai/llama.cpp/ (gitignored; prebuilt Windows CUDA binaries
# live under llama.cpp/bin/extracted/, no build required)
# Weights: ai/providers/bonsai/model/Bonsai-27B-Q1_0.gguf (gitignored, *.gguf)
# from https://huggingface.co/prism-ml/Bonsai-27B-gguf
# Start the server first, from ai/providers/bonsai/llama.cpp/bin/extracted/:
#   ./llama-server.exe -m ../../../model/Bonsai-27B-Q1_0.gguf --host 0.0.0.0 --port 8080 -ngl 99
BONSAI_BASE_URL   = "http://localhost:8080/v1"
BONSAI_MODEL      = "Bonsai-27B-Q1_0"  # sent in the request; llama-server ignores it and serves whatever's loaded
BONSAI_MAX_TOKENS = 4096

# Per-request reasoning budget (thinking_budget_tokens) — only honored by the
# server when it was NOT started with its own --reasoning-budget flag (see
# start_bonsai.bat, and server-common.cpp's reasoning-budget handling: the
# server-side flag wins whenever set, the per-request field is the fallback).
# Mirrors ANTHROPIC_MODEL_HEAVY/_FAST's pass 3/4 split: type inference and
# class reconstruction benefit from more thinking than the more mechanical
# cleanup/renaming/consistency/beautification passes.
BONSAI_REASONING_BUDGET_HEAVY = 600  # passes 3, 4
BONSAI_REASONING_BUDGET_FAST  = 200  # passes 1, 2, 5, 6

MAX_TOKENS        = 8192  # Anthropic / Xiaomi
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

# --- Pipeline ---
NUM_PASSES = 6
PASS_NAMES = [
    "cleanup",           # Pass 1: structural cleanup
    "renaming",          # Pass 2: variable/function renaming
    "type_inference",    # Pass 3: struct/enum/typedef recovery
    "class_reconstruction",  # Pass 4: C++ class/vtable recovery
    "consistency",       # Pass 5: cross-function naming alignment
    "beautification",    # Pass 6: final C++ polish
]
