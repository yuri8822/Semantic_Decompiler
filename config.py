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
# Set LLM_PROVIDER to "anthropic", "xiaomi", or "ollama"
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

MAX_TOKENS        = 8192  # Anthropic / Xiaomi
OLLAMA_MAX_TOKENS = 4096  # local models typically cap at 4k
AI_TIMEOUT_SECONDS = 120
DECOMPILER_TIMEOUT_SECONDS = 60

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
