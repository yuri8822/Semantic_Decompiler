# Semantic Decompiler

An AI-assisted reverse engineering tool that goes beyond traditional decompilation. Instead of producing raw pseudocode, it uses a multi-pass AI pipeline to reconstruct **semantically meaningful C++** — with recovered names, inferred types, and documented intent.

```
Binary → Ghidra analysis → IR normalization → Multi-pass AI reconstruction → Recovered C++ project
```

---

## How it works

Traditional decompilers stop at pseudocode. This tool uses that pseudocode as a starting point and runs it through a pipeline of focused AI passes:

| Pass | Goal |
|------|------|
| 1 — Cleanup | Remove compiler boilerplate, simplify expressions |
| 2 — Renaming | Infer meaningful names from strings, API calls, patterns |
| 3 — Type inference | Recover structs, enums, typedefs from pointer arithmetic |
| 4 — Class reconstruction | Identify C++ classes, vtables, constructors |
| 5 — Consistency | Align names and types across caller/callee boundaries |
| 6 — Beautification | Final C++ polish, const qualifiers, doc comments |

Each pass feeds its output into the next. Context accumulates in a SQLite semantic database across functions — types recovered in one function inform the analysis of another.

---

## Prerequisites

- **Python 3.11+**
- **Ghidra** — headless analyzer, available at [ghidra-sre.org](https://ghidra-sre.org/)
- An LLM provider (at least one):
  - [Anthropic](https://console.anthropic.com) — API key required
  - [Xiaomi MiMo](https://platform.xiaomimomo.com) — API key required
  - [Ollama](https://ollama.com) — local, no key needed

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

# Default provider: "anthropic", "xiaomi", or "ollama"
LLM_PROVIDER = "anthropic"

# Anthropic models (heavy for passes 3-4, fast for the rest)
ANTHROPIC_MODEL_HEAVY = "claude-opus-4-8"
ANTHROPIC_MODEL_FAST  = "claude-sonnet-4-6"

# Xiaomi MiMo
XIAOMI_MODEL = "mimo-v2.5-pro"

# Ollama (local)
OLLAMA_MODEL = "carstenuhlig/omnicoder-9b:q4_k_m"
```

---

## Usage

```bash
python main.py <binary> [options]
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--provider` | `anthropic` | LLM provider: `anthropic`, `xiaomi`, `ollama` |
| `--passes` | `6` | Number of AI passes (1–6) |
| `--limit` | `0` (all) | Process only the first N functions |
| `--output` | `output/recovered` | Output directory |
| `--skip-ghidra` | — | Reuse existing Ghidra JSON export |
| `--verbose` | — | Stream Ghidra output to stdout |

**Examples:**

```bash
# Full 6-pass run on a binary using Anthropic
python main.py target.exe --provider anthropic

# Quick 3-pass test on the first 10 functions using Xiaomi MiMo
python main.py target.exe --provider xiaomi --passes 3 --limit 10

# Local run with Ollama, reusing a prior Ghidra export
python main.py target.exe --provider ollama --skip-ghidra

# Analyze a Windows system binary
python main.py "C:\Windows\System32\find.exe" --provider xiaomi --passes 3 --limit 5
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
Best output quality. Uses `claude-opus-4-8` for type inference and class reconstruction passes, `claude-sonnet-4-6` for lighter passes.

```bash
python main.py target.exe --provider anthropic
```

### Xiaomi MiMo
Good quality, lower cost. Uses `mimo-v2.5-pro` for all passes via an Anthropic-compatible API.

```bash
python main.py target.exe --provider xiaomi
```

### Ollama (local)
Free, fully offline. Quality depends on the model. Recommended: `qwen2.5-coder:7b` or `deepseek-coder-v2:16b`.

```bash
ollama pull qwen2.5-coder:7b
python main.py target.exe --provider ollama
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
  cfg_builder.py            — Builds CFG summaries (loops, branches, complexity)
  types_db.py               — SQLite semantic memory (types, summaries, call graph)
  known_apis.py             — Curated Windows/CRT API signature database

ai/
  translator.py             — Runs the multi-pass pipeline per function
  llm_client.py             — Unified client for Anthropic / Xiaomi / Ollama
  prompts.py                — Per-pass system prompts and user prompt builder

output/
  writer.py                 — Writes recovered.h, recovered.cpp, function_index.txt

ghidra_scripts/
  ExportAnalysis.java       — Ghidra script: exports functions, P-Code, CFG to JSON
```

---

## Design principles

- **Static analysis is authoritative.** Ghidra's decompilation, CFG, and signatures are ground truth. The AI enhances semantics — it never invents logic.
- **Function-level translation.** Each function is processed independently with its call-graph context, avoiding context window limits on large binaries.
- **Semantic memory.** Types and summaries persist across functions. A struct discovered in one function automatically becomes context for its callers and callees.
- **Provider-agnostic.** The same prompts run unchanged against any supported provider.

---

## Limitations

- Output quality depends heavily on the LLM and number of passes. More passes = better results but more cost/time.
- Heavily optimized or obfuscated binaries produce degraded Ghidra output that limits AI recovery.
- C++ virtual dispatch and RTTI recovery is best-effort — pass 4 infers from patterns, not RTTI tables.
- Generated code compiles conceptually but is not guaranteed to be link-compatible with the original.
