# Build Progress

## Status: Phase 2 complete — pipeline is end-to-end runnable

---

## Full file inventory

| File | Status | Notes |
|---|---|---|
| `config.py` | Done | Central config — paths, model names, pass count |
| `requirements.txt` | Done | `anthropic`, `networkx`, `pydantic`, `rich`, `sqlite-utils` |
| `analyzer/__init__.py` | Done | Package marker |
| `analyzer/ghidra_runner.py` | Done | Headless runner with `cwd`, `-overwrite`, error output |
| `analyzer/parse_output.py` | Done | Pydantic v2 models for full JSON schema |
| `analyzer/ir_builder.py` | Done | Full p-code parser → human-readable IR |
| `analyzer/cfg_builder.py` | Done | networkx CFG: loops, branches, cyclomatic complexity |
| `analyzer/types_db.py` | Done | 4-table semantic memory, WAL mode, context queries |
| `ai/__init__.py` | Done | Package marker |
| `ai/prompts.py` | Done | 6 pass-specific system prompts + context assembler |
| `ai/translator.py` | Done | Multi-pass Claude pipeline, type harvesting, summaries |
| `output/__init__.py` | Done | Package marker |
| `output/writer.py` | Done | Writes `recovered.h`, `recovered.cpp`, `function_index.txt` |
| `ghidra_scripts/ExportAnalysis.java` | Done | Full export: CFG, strings, p-code SSA, callers/callees |
| `main.py` | Done | CLI with argparse, rich progress bars, full pipeline wiring |

---

## How to run

### Prerequisites
1. Ghidra installed at `C:\ghidra\` (or update `config.py`)
2. `ANTHROPIC_API_KEY` set in environment
3. Dependencies installed: `pip install -r requirements.txt`

### VS Code interpreter warning
The IDE flags `rich` imports as unresolved — this is a VS Code interpreter
mismatch, not a real error. Fix: `Ctrl+Shift+P` → **Python: Select Interpreter**
→ `C:\Users\umar\AppData\Local\Programs\Python\Python311\python.exe`

### Basic run
```
python main.py data/binaries/sample.exe
```

### Skip Ghidra (re-use existing export)
```
python main.py data/binaries/sample.exe --skip-ghidra
```

### Faster iteration (fewer passes, first 10 functions only)
```
python main.py data/binaries/sample.exe --passes 2 --limit 10
```

### Output
```
output/recovered/<binary_name>/
    recovered.h          — type defs + forward declarations
    recovered.cpp        — full function implementations
    function_index.txt   — address → name mapping
```

---

## What was built — session 2

### `analyzer/ghidra_runner.py`
- Sets `cwd=PROJECT_ROOT` so the Java script's relative `data/ghidra_json/` path resolves correctly
- Derives the expected JSON output path using the same sanitisation regex as the Java script
- Passes `-overwrite` to handle re-analysis of an already-imported binary
- Captures stderr and surfaces the last 2 KB on failure

### `analyzer/parse_output.py`
- Full pydantic v2 models: `Parameter`, `CFGBlock`, `CFGEdge`, `CFG`, `FunctionData`
- Handles `from`/`to`/`type` JSON keys (Python keywords/builtins) via field aliases
- Skips malformed function entries with a warning instead of crashing
- `FunctionData.to_context_dict()` produces the flat dict the translator expects

### `analyzer/ir_builder.py`
- Parses all common Ghidra p-code mnemonics into readable form
- Handles: LOAD/STORE (pointer notation), CALL/CALLIND (with args), BRANCH/CBRANCH
  (goto / conditional), arithmetic/bitwise (infix), comparisons, casts, RETURN
- Emits unparseable ops as `; raw` comments so no op is silently dropped
- Works with both pydantic objects and plain dicts

### `analyzer/cfg_builder.py`
- Builds a networkx `DiGraph` from the exported CFG
- Reports: block/edge count, entry point, loop count (via `nx.simple_cycles`),
  conditional branch count, unreachable blocks, cyclomatic complexity
- Summary injects directly into the AI prompt for passes 1–4

### `output/writer.py`
- Extracts struct/class/enum/typedef definitions from all functions and deduplicates
  them into `recovered.h`
- Extracts forward declarations using a comment-aware signature parser
- Writes clean `recovered.cpp` with address banners above each function
- Writes `function_index.txt` sorted by address

### `main.py`
- 4-step progress display: Ghidra → Load → DB init → AI reconstruction
- Rich progress bar with function name, count, and elapsed time
- Checks `ANTHROPIC_API_KEY` at startup before doing any work
- `--skip-ghidra` reuses existing JSON; `--limit N` for quick test runs
- All errors surface clearly with context before exiting

---

## Known limitations / next steps

### Still to build
| Item | Priority |
|---|---|
| `data/binaries/` sample binary for testing | Needed to do a real run |
| RTTI recovery pass (vtables, `type_info`) | High value for C++ binaries |
| Symbolic execution hook (angr) | Future — constant propagation, path reasoning |
| Recompile-and-compare validation | Future — automated correctness scoring |

### Known edge cases
1. **p-code parser coverage** — `MULTIEQUAL` (SSA φ-node), `INDIRECT`, `SEGMENTOP`,
   `CPOOLREF`, `NEW` are not explicitly handled and fall to the generic fallback.
   They still emit correctly as `; raw`, so the AI sees them — just less prettily.

2. **Type harvesting regex** — catches `struct/class/enum` blocks with a single `}`
   level. Nested types (e.g. struct containing an anonymous union) and C++ templates
   will not be extracted. Good enough for C-compiled binaries.

3. **Context window budget** — p-code is capped at 3000 chars in `prompts.py`.
   Very large functions will be truncated. Future: summarise p-code by opcode
   frequency rather than raw truncation.

4. **Call graph query** — `get_callee_summaries` joins on function name, not address.
   If two functions share a name (unlikely but possible in stripped binaries), the
   wrong summary could be injected. Future: join on address.

5. **analyzeHeadless path** — hardcoded to `C:\ghidra\support\analyzeHeadless.bat`
   in `config.py`. Update this before running.
