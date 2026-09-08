# Semantic Decompiler

An AI-assisted reverse engineering tool that goes beyond traditional decompilation. Instead of producing raw pseudocode, it feeds a single, richly-evidenced AI reconstruction pass to produce **semantically meaningful C++** — with recovered names, inferred types, and documented intent.

```
Binary → Ghidra analysis → IR normalization → Single-pass AI reconstruction → Recovered C++ project
```

---

## How it works

Traditional decompilers stop at pseudocode. This tool uses that pseudocode as a starting point and, in one pass, asks the model to do the full reconstruction — cleanup, renaming, type inference, class reconstruction, cross-function consistency, and beautification — together, backed by as much ground-truth evidence as static analysis can produce:

- **Deterministic evidence** — calling convention, real dominator-based loop analysis, propagated constants, and pointer-aliasing hints, all computed by static analysis, not guessed by the AI.
- **Known-library hints** — detected STL/common-library usage, so the model reuses the real type instead of reinventing its internals.
- **Whole-program context** — classes and types already recovered elsewhere in the same binary.
- **Callee/caller summaries** — what a function's neighbors in the call graph are already known to do.
- **P-code IR and CFG summary** — a readable rendering of the actual instruction-level control flow.

A SQLite knowledge graph persists all of this across functions and across runs — types and summaries recovered in one function inform the analysis of another, and the same graph feeds richer context into every subsequent function in a run.

---

## Prerequisites

- **Python 3.11+**
- **Ghidra** — headless analyzer, available at [ghidra-sre.org](https://ghidra-sre.org/)
- An LLM provider (at least one):
  - [Anthropic](https://console.anthropic.com) — API key required
  - [Xiaomi MiMo](https://platform.xiaomimomo.com) — API key required
  - [Ollama](https://ollama.com) — local, no key needed
  - [llama.cpp](https://github.com/ggml-org/llama.cpp) — local, no key needed; run `llama-server` on port 8080
    with any GGUF and point the pipeline at it

---

## Installation

```bash
git clone <repo-url>
cd Semantic_Decompiler
pip install -r requirements.txt
```

Copy the environment template and fill in your keys:

```bash
copy .env.example .env   # Windows
cp .env.example .env     # Linux/Mac
```

`.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
XIAOMI_API_KEY=sk-s...
```

---

## Configuration

Edit [`config.py`](config.py) for your environment:

```python
# Path to Ghidra's headless analyzer
GHIDRA_PATH = r"C:\path\to\ghidra\support\analyzeHeadless.bat"

# Default provider: "anthropic", "xiaomi", "ollama", "llamacpp", or "deepseek"
LLM_PROVIDER = "anthropic"

# Anthropic models (heavy for the reconstruction pass, fast for the one-line summary)
ANTHROPIC_MODEL_HEAVY = "claude-opus-4-8"
ANTHROPIC_MODEL_FAST  = "claude-sonnet-4-6"

# Xiaomi MiMo
XIAOMI_MODEL = "mimo-v2.5-pro"

# Ollama (local)
OLLAMA_MODEL = "carstenuhlig/omnicoder-9b:q4_k_m"

# llama.cpp (local) — whatever GGUF llama-server currently has loaded
LLAMACPP_BASE_URL = "http://localhost:8080/v1"
```

---

## Usage

```bash
python main.py <binary> [options]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--provider` | `anthropic` | LLM provider: `anthropic`, `xiaomi`, `ollama`, `llamacpp`, `deepseek` |
| `--limit` | `0` (all) | Process only the first N functions |
| `--output` | `output/recovered` | Output directory |
| `--skip-ghidra` | — | Reuse existing Ghidra JSON export (now automatic if one exists — kept for explicitness) |
| `--force-ghidra` | — | Re-run Ghidra even if an export already exists |
| `--restart` | — | Ignore all resume/cache state and retranslate every function |
| `--verbose` | — | Stream Ghidra output to stdout |

**Examples:**

```bash
# Full run on a binary using Anthropic
python main.py target.exe --provider anthropic

# Quick test on the first 10 functions using Xiaomi MiMo
python main.py target.exe --provider xiaomi --limit 10

# Local run with Ollama, reusing a prior Ghidra export
python main.py target.exe --provider ollama --skip-ghidra

# Local run against a llama.cpp server on port 8080
python main.py target.exe --provider llamacpp --skip-ghidra

# Analyze a Windows system binary
python main.py "C:\Windows\System32\find.exe" --provider xiaomi --limit 5
```

---

## Output

Results are written to `output/recovered/<binary_name>/`:

```
recovered.h            — Forward declarations + recovered type definitions
recovered.cpp          — Full AI-reconstructed function implementations
function_index.txt     — Address → recovered name index
```

The semantic database (`semantic.db`) persists across runs — types and summaries discovered in one run inform subsequent runs on the same binary.

---

## Providers

### Anthropic
Best output quality. Uses `claude-opus-4-8` for the reconstruction pass, `claude-sonnet-4-6` for the lightweight one-line summary.

```bash
python main.py target.exe --provider anthropic
```

### Xiaomi MiMo
Good quality, lower cost. Uses `mimo-v2.5-pro` for every call via an Anthropic-compatible API.

```bash
python main.py target.exe --provider xiaomi
```

### Ollama (local)
Free, fully offline. Quality depends on the model. Recommended: `qwen2.5-coder:7b` or `deepseek-coder-v2:16b`.

```bash
ollama pull qwen2.5-coder:7b
python main.py target.exe --provider ollama
```

### llama.cpp (local)
Free, fully offline, model-agnostic. Serve any GGUF with `llama-server` on port
8080 and the pipeline talks to its OpenAI-compatible endpoint — no
model-specific request options are sent, so whatever's loaded is what runs.

Neither the engine nor any weights live in this repo: install llama.cpp
system-wide (prebuilt releases are enough — no compiler/MSVC needed) and make
sure `llama-server` resolves on `PATH`.

```bash
# start the server with whatever GGUF you want
llama-server --model /path/to/model.gguf --host 0.0.0.0 --port 8080 -ngl 99

# on Windows, set MODEL in start_llamacpp.bat and run that instead

# in another terminal
python main.py target.exe --provider llamacpp
```

---

## Architecture

```
main.py                     — Orchestrates the full pipeline
config.py                   — All runtime configuration

analyzer/
  ghidra_runner.py          — Invokes Ghidra headless analysis
  parse_output.py           — Parses Ghidra JSON export into typed models
  ir_builder.py             — Converts P-Code to readable IR for AI context
  cfg_builder.py            — CFG summaries + deterministic evidence (loops, constants, aliasing)
  library_signatures.py     — STL/known-library detection
  types_db.py               — SQLite knowledge graph (entities, facts, relationships, confidence)
  known_apis.py             — Curated Windows/CRT API signature database

ai/
  translator.py             — Gathers evidence and runs the single reconstruction pass per function
  llm_client.py             — Thin facade that picks a provider and forwards complete()
  prompts.py                — The single system prompt and user-prompt builder
  providers/
    base.py                 — BaseProvider ABC (one method: complete)
    anthropic/anthropic_provider.py — Claude, heavy/fast model split (reconstruction vs. summary)
    xiaomi/xiaomi_provider.py       — MiMo, Anthropic-compatible API
    ollama/ollama_provider.py       — Local, OpenAI-compatible endpoint
    llamacpp/llamacpp_provider.py   — Local llama-server on port 8080, OpenAI-compatible endpoint
    deepseek/deepseek_provider.py   — DeepSeek, OpenAI-compatible API

output/
  writer.py                 — Writes recovered.h, recovered.cpp, function_index.txt

ghidra_scripts/
  ExportAnalysis.java       — Ghidra script: exports functions, P-Code, CFG to JSON
```

---

## Design principles

- **Static analysis is authoritative.** Ghidra's decompilation, CFG, and signatures are ground truth. The AI enhances semantics — it never invents logic.
- **Function-level translation.** Each function is processed independently with its call-graph context, avoiding context window limits on large binaries.
- **Knowledge graph, not just memory.** Types, summaries, and deterministic evidence persist across functions and runs in a SQLite knowledge graph — a struct discovered in one function automatically becomes context for its callers and callees.
- **One well-evidenced pass, not many thin ones.** A single reconstruction call, backed by as much ground-truth static-analysis evidence as can be gathered, rather than splitting the work across a sequential pipeline that can drift or contradict itself across steps.
- **Provider-agnostic.** The same prompt runs unchanged against any supported provider.

---

## Limitations

- Output quality depends heavily on the underlying LLM.
- Heavily optimized or obfuscated binaries produce degraded Ghidra output that limits AI recovery.
- C++ virtual dispatch and RTTI recovery is best-effort — inferred from patterns, not RTTI tables.
- Generated code compiles conceptually but is not guaranteed to be link-compatible with the original, and nothing in the pipeline currently attempts to actually compile the output.
