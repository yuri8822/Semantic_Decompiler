import json
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

# --- Settings overrides (TUI Settings screen) ---------------------------
# The values above are this file's hardcoded defaults. settings.json (gitignored,
# per-machine — same reason GHIDRA_PATH itself is per-machine) holds whatever
# the Settings screen has saved and, if present, overrides the matching names
# below. This file is only read here, once, at import time — every other
# module's `from config import X` binds the post-override value, so a saved
# change only takes effect on the next process start, not the current one.
SETTINGS_PATH = PROJECT_ROOT / "settings.json"

# Whitelist of names the Settings screen may persist — deliberately excludes
# path/derived constants above (DB_PATH, GHIDRA_JSON_DIR, OUTPUT_DIR, etc.)
# since those aren't independent settings, just plain scalars a user would
# reasonably want to edit instead of hand-editing this file.
SETTINGS_KEYS = (
    "GHIDRA_PATH",
    "LLM_PROVIDER",
    "ANTHROPIC_MODEL_HEAVY", "ANTHROPIC_MODEL_FAST",
    "XIAOMI_BASE_URL", "XIAOMI_MODEL",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "BONSAI_BASE_URL", "BONSAI_MODEL", "BONSAI_MAX_TOKENS",
    "MAX_TOKENS", "OLLAMA_MAX_TOKENS",
    "AI_TIMEOUT_SECONDS", "DECOMPILER_TIMEOUT_SECONDS",
    "NUM_PASSES",
)

# Snapshot of this file's hardcoded values, taken BEFORE overrides are
# applied — lets the Settings screen's "Reset to Defaults" show the true
# code defaults immediately, without needing to re-import this module.
HARDCODED_DEFAULTS = {key: globals()[key] for key in SETTINGS_KEYS}


def _load_settings_overrides() -> None:
    if not SETTINGS_PATH.exists():
        return
    try:
        overrides = json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return
    for key, value in overrides.items():
        if key in SETTINGS_KEYS:
            globals()[key] = value


_load_settings_overrides()
