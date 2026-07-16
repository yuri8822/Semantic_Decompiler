# Build Progress

## Status: Phase 2 complete — pipeline is end-to-end runnable

---

## Full file inventory

| File | Status | Notes |
|---|---|---|
| `config.py` | Done | Central config — paths, model names, pass count |
| `requirements.txt` | Done | `anthropic`, `openai`, `networkx`, `pydantic`, `rich`, `sqlite-utils` |
| `analyzer/__init__.py` | Done | Package marker |
| `analyzer/ghidra_runner.py` | Done | Headless runner: absolute output-dir script arg, `-overwrite`, error output |
| `analyzer/parse_output.py` | Done | Pydantic v2 models for full JSON schema |
| `analyzer/ir_builder.py` | Done | Full p-code parser → human-readable IR |
| `analyzer/cfg_builder.py` | Done | networkx CFG: loops, branches, cyclomatic complexity |
| `analyzer/types_db.py` | Done | 5-table semantic memory, WAL mode, context queries |
| `ai/__init__.py` | Done | Package marker |
| `ai/prompts.py` | Done | 6 pass-specific system prompts + context assembler |
| `ai/translator.py` | Done | Multi-pass LLM pipeline (Anthropic/Xiaomi/Ollama), type harvesting, summaries |
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
- Ghidra's JVM working directory is unpredictable, so instead of relying on `cwd`,
  the absolute output directory is passed to `ExportAnalysis.java` as a script arg
- Derives the expected JSON output path using the same sanitisation regex as the Java script
- Passes `-overwrite` to handle re-analysis of an already-imported binary
- If the export file is missing after a run, surfaces the last 3 KB of Ghidra's
  **stdout** in the error (a non-zero exit code alone just points the user at `--verbose`)

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
- Forward declarations prefer Ghidra's own signature (authoritative types/param count)
  with the `FUN_xxx` placeholder swapped for the AI-chosen name; only falls back to a
  comment-aware heuristic parse of the AI-generated code when Ghidra gave no signature
- Writes clean `recovered.cpp` with address banners above each function
- Writes `function_index.txt` sorted by address

### `main.py`
- 4-step progress display: Ghidra → Load → DB init → AI reconstruction
- Rich progress bar with function name, count, and elapsed time
- Checks the API key for whichever `--provider` was selected (`ANTHROPIC_API_KEY` for
  anthropic, `XIAOMI_API_KEY` for xiaomi) at startup before doing any work; no check
  for ollama, which needs no key
- `--skip-ghidra` reuses existing JSON; `--limit N` for quick test runs
- All errors surface clearly with context before exiting

---

## What was built — session 3

Fixed a crash and two silent-corruption failure modes in the multi-pass loop,
surfaced by tracing through what happens across all 6 passes rather than
testing each pass in isolation.

### `analyzer/types_db.py`
- Added the missing `pass6_output` column. Previously `NUM_PASSES = 6` but the
  `functions` table only had `pass1_output`…`pass5_output`, so `set_pass_output`
  raised `sqlite3.OperationalError` on every full 6-pass run. **Existing local
  `semantic.db` files predate this — delete/regenerate them, there is no
  migration path (`CREATE TABLE IF NOT EXISTS` won't add columns to an
  existing table).**
- Added `ai_name` column + `set_ai_name`/`get_ai_name` to persist the name the
  AI settles on in pass 2.

### `ai/translator.py`
- **Name lock:** pass 2 is the only pass allowed to rename the function. After
  it runs, `extract_function_name()` captures whatever name the AI chose and
  that name is force-injected into every later prompt as a "LOCKED FUNCTION
  NAME" instruction, so passes 3–6 can't silently drift back to the raw
  Ghidra name.
- **Callee guard:** after each pass ≥3, the pipeline diffs which known callees
  (from the Ghidra call-graph export) disappeared from the code between the
  previous pass and this one. If a call vanished, the pass's output is
  rejected and the previous pass's code is kept instead — a cheap defense
  against the AI silently dropping a call during type/class/beautification
  work. Note: this only catches callees whose *original* Ghidra name is still
  literally present in the prior pass's code — once a callee is renamed
  (typically in pass 2), it drops out of the check for all later passes.
- `extract_function_name()` is now the single, comment-aware implementation
  (tracks multi-line `/* ... */` blocks so a parenthesized word inside a doc
  comment can't be mistaken for the function's real name) — `main.py`
  previously had its own separate, less careful copy of this same logic;
  it now imports the shared one.

### `ai/llm_client.py`
- `_anthropic_complete` / `_xiaomi_complete` now scan `msg.content` for the
  first `text`-typed block instead of assuming `content[0]` is always text.

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
   `CPOOLREF`, `NEW` are not explicitly handled and fall to the generic
   `dst = MNEMONIC(args)` fallback in `_fmt_op`, not a `; raw` passthrough — that
   only happens if parsing the raw p-code string itself throws, which is rare.
   The AI still sees the operands, just without a hand-tuned rendering.

2. **Type harvesting regex** — catches `struct/class/enum` blocks with a single `}`
   level. Nested types (e.g. struct containing an anonymous union) and C++ templates
   will not be extracted. Good enough for C-compiled binaries.

3. **Context window budget** — p-code is capped at 3000 chars in `prompts.py`.
   Very large functions will be truncated. Future: summarise p-code by opcode
   frequency rather than raw truncation.

4. **Call graph query** — `get_callee_summaries` joins on function name, not address.
   If two functions share a name (unlikely but possible in stripped binaries), the
   wrong summary could be injected. Future: join on address.

5. **analyzeHeadless path** — `GHIDRA_PATH` in `config.py` is a machine-specific
   absolute path (currently a local dev path). Update it before running on
   another machine.

6. **Callee guard blind spot** — the pass ≥3 callee-drop check (see session 3,
   `ai/translator.py`) only fires while a callee's *original* Ghidra name is
   still literally present in the code. Once pass 2 renames a callee's call
   site, that callee is unprotected for the rest of the pipeline. Future:
   track renamed callee names too, not just the original list.
