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
| `ai/llm_client.py` | Done | Thin facade — picks a provider from `ai/providers/` and forwards `complete()` |
| `ai/providers/base.py` | Done | `BaseProvider` ABC — the one method (`complete`) every provider implements |
| `ai/providers/anthropic/anthropic_provider.py` | Done | Heavy/fast model split for passes 3/4 vs the rest |
| `ai/providers/xiaomi/xiaomi_provider.py` | Done | Anthropic-compatible API, one model for all passes |
| `ai/providers/ollama/ollama_provider.py` | Done | Local, OpenAI-compatible endpoint, one configured model |
| `ai/providers/bonsai/bonsai_provider.py` | Done | Local Bonsai 27B (1-bit) via llama.cpp's OpenAI-compatible server; strips a leading `<think>` block |
| `ai/providers/bonsai/llama.cpp/` | Done | Vendored PrismML fork (gitignored) + prebuilt Windows CUDA binaries under `bin/extracted/` |
| `ai/translator.py` | Done | Multi-pass LLM pipeline (provider-agnostic), type harvesting, summaries |
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

## What was built — session 4

Split the single `ai/llm_client.py` if/elif ladder into one file per
provider so each backend's quirks can be tuned in isolation, and added a
fourth provider: Bonsai 27B (1-bit, local).

### `ai/providers/`
- `base.py` — `BaseProvider` ABC, one abstract method: `complete(system, user, pass_num) -> str`
- `anthropic_provider.py`, `xiaomi_provider.py`, `ollama_provider.py` — straight
  extractions of the logic that used to live in `llm_client.py`, behavior unchanged
  (including the pass-3/4 heavy-model split for Anthropic, and the msg.content
  block-type scan from session 3)
- `bonsai_provider.py` — new. Talks to a local llama.cpp server (must be
  [PrismML's fork](https://github.com/PrismML-Eng/llama.cpp) — vanilla llama.cpp
  doesn't have the `Q1_0_g128` hybrid-attention kernels this quantization needs)
  over its OpenAI-compatible `/v1/chat/completions` endpoint, same shape as the
  Ollama provider. Strips a leading `<think>...</think>` block from the response,
  since PrismML's docs say the 27B variant serves with "thinking" enabled by
  default — **unverified against a live server**, see edge case below.
- `__init__.py` — `_REGISTRY` dict + `get_provider(name, ollama_model=None)` factory

### `ai/llm_client.py`
- Reduced to a thin facade: `LLMClient.__init__` resolves a provider via
  `get_provider()` and `complete()` just forwards to it. Public constructor
  signature (`provider`, `ollama_model`) is unchanged, so `ai/translator.py`
  and `main.py` needed no changes.

### `config.py` / `main.py`
- Added `BONSAI_BASE_URL`, `BONSAI_MODEL`, `BONSAI_MAX_TOKENS`; `LLM_PROVIDER`
  and `--provider` now both accept `"bonsai"`.

---

## What was built — session 5

Reorganized each provider into its own subfolder (rather than a flat file),
and got Bonsai actually runnable locally.

### `ai/providers/`
- Each provider is now `ai/providers/<name>/<name>_provider.py` with its own
  `__init__.py` (e.g. `ai/providers/anthropic/anthropic_provider.py`), not a
  flat file directly under `providers/`. `base.py` stays directly under
  `ai/providers/` since it's shared. `ai/providers/__init__.py`'s imports were
  updated to the new module paths; nothing else needed to change since the
  `ai.providers.base` import path didn't move.
- `ai/providers/bonsai/llama.cpp/` — PrismML's llama.cpp fork is now vendored
  *inside* the provider's own folder instead of the project root (still
  gitignored — see note below on why the same pattern still works after the move).
  Prebuilt Windows CUDA binaries (no MSVC/build step needed) live at
  `ai/providers/bonsai/llama.cpp/bin/extracted/llama-server.exe`, downloaded
  from the fork's GitHub releases (`llama-prism-*-bin-win-cuda-12.4-x64.zip`
  + `cudart-llama-bin-win-cuda-12.4-x64.zip`).

### `.gitignore`
- No change needed. The existing `llama.cpp/` entry has only a trailing slash
  (no leading/middle slash), so per gitignore's matching rules it already
  matches a `llama.cpp` directory at any depth, not just at the project root.

---

## What was built — session 6

First real end-to-end test run (`--provider bonsai --skip-ghidra --limit 1`
against `find.exe`'s `FUN_140001010`) surfaced a genuine bug in the session-3
callee guard, independent of which provider is used.

### What the test run found
Traced the corruption pass-by-pass via `semantic.db`'s `pass1_output` ...
`pass6_output` columns. Passes 1-2 were fine (real logic preserved). Pass 3
(type inference) violated its own system prompt — instead of adding type defs
*above* the function, it deleted the function body entirely, leaving only
`extern` declarations reusing the callees' names. Pass 4 found no body to
work from and fabricated one from scratch (`push reg r13, [0]` repeated 17
times — not present anywhere in the real p-code or decompiled input; pure
hallucination). Passes 5-6 carried the fabricated body forward unchanged.

The session-3 callee guard didn't catch this because it only checked whether
a callee's name appeared *anywhere in the text*, not whether it was actually
*called*. Pass 3's broken output still contained `extern void
__set_app_type(int app_type);` etc. — the names were technically "present,"
just as dead declarations instead of live calls — so the guard saw no drop.

### `ai/translator.py`
- Replaced `_dropped_callees`'s bare-name-presence check with a real
  call-site check (`_is_call_site`): a line only counts as evidence the
  callee is used if `name(` appears there and the line doesn't match a
  `[extern] TYPE name(` signature pattern (forward declaration or
  definition). A callee now has to have gone from "actually called somewhere
  in `before`" to "not called anywhere in `after`" to trigger a revert.
- Verified against the real captured DB history from the test run above: the
  fixed check correctly flags all 4 callees as dropped at the pass 2 -> pass 3
  transition (where the old check saw nothing wrong), and produces zero false
  positives against unchanged code with real call sites.
- This fix is provider-agnostic — it lives in the shared multi-pass loop, not
  in any provider file, so it protects against this failure mode regardless
  of which backend (Anthropic/Xiaomi/Ollama/Bonsai) produced the bad pass.

### Open question this test raised, not yet answered
Whether Bonsai 27B (1-bit)'s output quality is usable for this pipeline at
all is still unresolved — this was one small function on a heavily quantized
model attempting a demanding structured task, and it violated the "AI must
not invent logic" invariant on the very first non-trivial pass. A fair
verdict needs the same function run through another provider for comparison
before concluding anything about Bonsai specifically (see edge case below).

---

## What was built — session 7

Provider-aware resume, step 1 of 4 (see `nifty-popping-steele.md` plan).
Motivation: a long unattended Bonsai run (planned: ~50 functions x 6 passes
at local inference speed) had zero resume capability — an interruption at
any point meant redoing every function from scratch, even ones that had
already finished cleanly.

### `analyzer/types_db.py`
- Added a `provider` column to `functions`, tracking which LLM backend
  produced the row's *current* results.
- Generalized the schema-migration problem instead of patching it again:
  `_FUNCTIONS_COLUMNS` is now the single source of truth for both the
  `CREATE TABLE` (new databases) and a new `_migrate_functions_columns()`
  step in `init()` that `ALTER TABLE ADD COLUMN`s anything missing from an
  existing database. This is the second time a new column broke existing
  local `semantic.db` files (first was `pass6_output`, session 3) — fixed
  properly this time so it doesn't happen a third time.
- Added `set_provider(address, provider)` and `is_complete_for_provider(address,
  provider) -> bool` (true only if `final_cpp` is non-empty *and* `provider`
  matches exactly — a result from a different provider doesn't count as done).

### `ai/translator.py`
- `MultiPassTranslator` now stores its own `provider` and calls
  `db.set_provider(address, self.provider)` at the start of `translate()`,
  before the pass loop. Existing upsert semantics on `set_pass_output`/
  `set_final_cpp` already overwrite stale data when a function legitimately
  gets reprocessed — no explicit wipe needed.

### `main.py`
- The per-function loop now checks `db.is_complete_for_provider(fn.address,
  args.provider.lower())` before calling `translator.translate()`. On a hit,
  it pulls `final_cpp`/`ai_name` straight from the DB and calls
  `writer.add_function()` without touching the LLM at all — shown in the
  progress bar as `(cached)`.

### Verified against the real database (not just synthetic data)
- Ran the schema migration against the actual `semantic.db` accumulated
  from earlier testing — confirmed the `provider` column was added cleanly
  with no data loss.
- The `find.exe` / `FUN_140001010` row already had a `final_cpp` from
  earlier Bonsai testing, but predated this feature, so its `provider` was
  blank — correctly did *not* count as done (fails toward reprocessing
  rather than assuming a match on unknown history). First real run after
  the change reprocessed it for real (~65s of actual LLM calls) and
  backfilled `provider='bonsai'`.
- Second real run of the identical command completed in **1.8 seconds** and
  showed `FUN_140001010 (cached)` in the progress bar — confirmed no request
  reached the running `llama-server` (timing alone rules it out; a real run
  takes over a minute).
- Confirmed provider-mismatch handling directly against the now-populated
  row: `is_complete_for_provider(addr, "bonsai")` → `True`,
  `is_complete_for_provider(addr, "anthropic")` → `False`,
  `is_complete_for_provider(addr, "ollama")` → `False`. (Couldn't run a live
  end-to-end `--provider anthropic` check — no Anthropic API key is
  configured in this environment — but the actual skip-decision function is
  the same one exercised above, just with a different provider string.)

### Remaining steps (not yet built, per the approved plan)
2. Incremental output writing — `writer.write()` currently only runs once,
   after the *entire* function loop finishes, so a crash before that point
   still loses all output files even though the DB has complete data.
3. Pass-level resume within a function — currently a function that gets
   reprocessed always restarts at pass 1, even if passes 1-3 already
   completed under the same provider.
4. Auto-detect Ghidra export reuse — `--skip-ghidra` still has to be passed
   manually; nothing yet checks whether an export already exists by default.

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

6. **Callee guard blind spot** — the pass ≥3 callee-drop check (`ai/translator.py`,
   hardened in session 6 to check real call sites instead of bare name
   presence — see session 6) only fires while a callee's *original* Ghidra
   name is still being called under that name. Once pass 2 renames a
   callee's call site, that callee is unprotected for the rest of the
   pipeline — this part of the gap is unchanged by the session 6 fix. Future:
   track renamed callee names too, not just the original list.

7. ~~Bonsai provider is untested against a live server~~ **Resolved 2026-07-17.**
   Verified against a real running server (`Bonsai-27B-Q1_0.gguf` via the
   PrismML fork): reasoning is on by default (Qwen3-style template) and
   arrives in `message.reasoning_content`, not inline `<think>` tags — and
   without a fix, a request could burn its entire `max_tokens` budget on the
   reasoning trace and return `content=""` (`finish_reason="length"`),
   silently feeding empty output into the translator. Fixed in
   `bonsai_provider.py` by passing `extra_body={"chat_template_kwargs":
   {"enable_thinking": False}}` on every request (suppresses reasoning
   entirely, confirmed `content` now has the real answer with
   `finish_reason="stop"`), plus a guard that raises `RuntimeError` if
   content ever comes back empty alongside a reasoning trace. The `model`
   field in the request does not need to match anything — confirmed the
   server ignores it and serves whatever's loaded regardless of the string sent.
