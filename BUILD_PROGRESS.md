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

### Remaining steps (per the approved plan)
~~2. Incremental output writing~~ — done, see session 8 below.
~~3. Pass-level resume within a function~~ — done, see session 9 below.
~~4. Auto-detect Ghidra export reuse~~ — done, see session 10 below.

All four steps of the provider-aware resume plan (`nifty-popping-steele.md`)
are now complete.

---

## What was built — session 10

Provider-aware resume, step 4 of 4 (final step — see sessions 7-9):
auto-detect Ghidra export reuse.

### `main.py`
- Added `--force-ghidra` (re-run Ghidra even if an export already exists).
- The Ghidra step now resolves `json_path` and checks for an existing export
  *before* deciding whether to run analysis, rather than only checking it
  inside the `--skip-ghidra` branch. Decision logic: run fresh if
  `--force-ghidra` was passed, or if neither `--skip-ghidra` nor an existing
  export apply; otherwise reuse whatever export exists. `--skip-ghidra`
  keeps working exactly as before (explicit, and still errors out the same
  way if no export exists) — it's just no longer the *only* way to skip
  Ghidra, since an existing export is now reused automatically by default.

### Verified with real Ghidra runs, not mocked
- Ran against the real `C:\Windows\System32\find.exe` (needed the actual
  binary on disk, not just its JSON export, since `--force-ghidra` has to
  invoke real analysis) with **no** `--skip-ghidra` flag: console showed
  `Skipping Ghidra (existing export found)`, completed in 2.1s total
  (Ghidra reuse + an already-cached function) — confirmed auto-detection
  works without needing the manual flag.
- Ran the same command with `--force-ghidra`: console showed `Running
  Ghidra headless analysis...` and took ~36s (a real headless Ghidra
  invocation, JVM startup included) — confirmed via the export file's
  modification time updating to match the run, proving a genuine fresh
  write happened rather than a silent no-op.

---

## What was built — session 9

Provider-aware resume, step 3 of 4 (see sessions 7-8 and
`nifty-popping-steele.md`): pass-level resume within a function.

### `ai/translator.py`
- `MultiPassTranslator.translate()` now checks, before running any passes,
  whether `db.get_function(address)`'s stored `provider` matches
  `self.provider`. If so, it scans `pass1_output` … up to `self.num_passes`
  for the longest *contiguous* run of already-stored passes (stopping at the
  first gap) and resumes right after it — reusing that stored code as
  `current_code` instead of restarting at pass 1. A stored pass belonging to
  a *different* provider is never reused; the scan only runs when the
  provider matches, exactly mirroring the session-7 skip logic's safety
  rule.
- Restores in-loop state correctly when skipping ahead: re-extracts `ai_name`
  from a stored `pass2_output` via the existing `extract_function_name()`
  (not just the persisted `ai_name` DB column, which only captures the "did
  a rename actually happen" case and would miss "extraction succeeded but
  the name didn't change"), and re-runs `_harvest_types()` against a stored
  `pass3_output` (idempotent — safe even if it already ran before an
  interruption).
- Logs `[translator] Resuming <name> from pass N (passes 1-N-1 already done
  by <provider>)` when a resume actually happens, for visibility.

### Verified against a real simulated interruption, not synthetic data
- Took a fully-completed function (`find.exe`'s `FUN_140001140`, 6/6 passes
  done under `bonsai`) and manually cleared `pass4_output`, `pass5_output`,
  `pass6_output`, `final_cpp`, and `summary` directly in `semantic.db` —
  simulating a crash immediately after pass 3 finished.
- First rerun attempt (server was temporarily down) still proved the
  critical thing: the log showed `Resuming FUN_140001140 from pass 4
  (passes 1-3 already done by bonsai)` before failing on an unrelated
  connection error — confirming the resume-point detection fired correctly
  even though the actual LLM call couldn't complete. The DB was left
  untouched by the failed attempt (pass 4-6 still empty), proving no
  corruption from a failed resume attempt either.
- Once the server was back up, reran the identical command: completed in
  1m21s total for 2 cached functions + 1 resumed one. Confirmed via the DB
  that passes 1-3 came back byte-identical in length to the values
  preserved before the simulated interruption (proving genuine reuse, not
  silent recomputation), passes 4-6 completed fresh, `final_cpp` was set,
  and a coherent summary was generated from the finished result.
- Read the actual final code, not just its presence/length: legitimate,
  well-structured CRT startup/dispatch logic survived the resume intact
  (guard-callback locking, static initializer finalizers, `_amsg_exit`/
  `_initterm`/`_cexit` calls). One real Bonsai output bug spotted in passing
  — `static_cast UINT (uReturnCode);` is missing its angle brackets
  (`static_cast<UINT>(...)`), invalid C++ — unrelated to the resume
  mechanism itself; no current guard checks basic syntax validity, same
  category as the `return 0;`-in-a-void-function issue found earlier.

---

## What was built — session 8

Provider-aware resume, step 2 of 4 (see session 7 and
`nifty-popping-steele.md`): incremental output writing.

### `main.py`
- `writer.write()` now runs after every single function (both the cache-hit
  branch and the freshly-processed branch), instead of only once after the
  entire loop finishes. `ProjectWriter.write()` was already safe to call
  repeatedly — it fully regenerates all three output files from its
  in-memory `self._functions` list each time, so this is just cheap extra
  disk I/O for small text files, not a design change to `output/writer.py`.

### Verified live, not just by inspection after the fact
- Ran `python main.py find.exe --provider bonsai --skip-ghidra --limit 3`
  in the background (mixes one cached function with two that need real
  translation). While the process was still actively working on the third
  function, checked `output/recovered/find/recovered.cpp` and
  `function_index.txt` on disk directly — both already reflected the two
  completed functions (`InitializeApplication`, `ParseCommandLineAndCopyOffset`),
  confirmed by their banner comments and index rows appearing mid-run,
  before the process had finished or been interrupted at all. This is the
  actual scenario the feature targets: a crash or Ctrl+C at any point now
  leaves real, current, reviewable output on disk rather than nothing.
- Also incidentally re-confirmed session 7's caching end to end: the first
  function reused its session-7 cached result (`provider='bonsai'` match),
  and its `ai_name` — `InitializeApplication` — differed from the name a
  much earlier test run had produced for the same function, a reminder that
  Bonsai's local, non-deterministic inference can pick a different name
  each time it's asked to actually translate a function (not a caching bug;
  confirmed by checking the DB directly against what was on disk).

---

## What was built — session 11

Static code review of all 4 provider-aware-resume steps (sessions 7-10),
no commands run — read-only audit first, fix applied after. Found one real
correctness bug and several smaller items (see edge cases below for the
ones left open).

### The bug: provider-switch + early interruption could mislabel stale data as done
`ai/translator.py`'s `translate()` used to call `self.db.set_provider(address,
self.provider)` immediately after determining `same_provider`, before any
actual work under the new provider had happened. Traced a concrete failure:
a function fully completed by `anthropic`, then rerun with `--provider
bonsai` — if interrupted in the first few seconds (before bonsai's pass 1
even finishes), the DB would end up with `provider='bonsai'` while
`final_cpp` still held the complete, untouched `anthropic` result.
`is_complete_for_provider` checks only "`final_cpp` non-empty AND `provider`
matches" — both true — so a later run would silently skip this function as
"already done via bonsai," when it's 100% stale anthropic output mislabeled,
with no error or warning.

### Fix — `analyzer/types_db.py` / `ai/translator.py`
- Added `SemanticDB.clear_pass_data(address)`: wipes `pass1_output` …
  `pass6_output`, `final_cpp`, `ai_name`, and `summary` for a function.
- `translate()` now calls `self.db.clear_pass_data(address)` *before*
  `set_provider()`, whenever `same_provider` is `False`. This closes the
  dangerous window regardless of exactly where an interruption lands: if it
  hits between the clear and the provider write, `final_cpp` is already
  empty (safe); if it hits right after, same thing. The same-provider resume
  path (session 9) is unaffected — `clear_pass_data` is only called when
  switching providers or seeing a function for the first time, never when
  `same_provider` is `True`, so Step 3's mid-function resume still works off
  the untouched stored passes.

### Other findings from the review — all now resolved (see below)
- ~~`ai_name` isn't re-persisted to the DB during a pass-level resume~~ —
  **fixed.** The resume-scan in `translate()` now calls `self.db.set_ai_name(
  address, ai_name)` when re-deriving `ai_name` from a stored `pass2_output`,
  mirroring the main loop's pass-2 handling exactly (only persists if a
  rename actually happened, and it's a harmless idempotent overwrite if it
  was already persisted correctly the first time).
- ~~The schema migration can't add columns needing `NOT NULL`/`UNIQUE`
  without a default~~ — **fixed properly.** `_migrate_functions_columns`
  tries the cheap `ALTER TABLE ADD COLUMN` path first for every missing
  column; anything that fails (a constrained or non-constant-default column)
  now falls back to `_rebuild_functions_table`: create a new table matching
  the current schema, copy over every column that already exists (a
  genuinely new column takes its schema default via SQL's normal
  omitted-column behavior), drop the old table, rename the new one into
  place. SQLite DDL is transactional, so this rolls back cleanly with the
  rest of `init()`'s connection block if anything fails partway.
  Verified against a synthetic old-style DB (missing both `provider` and the
  non-constant-default `analyzed_at`, in a scratch copy, not the real
  database): the existing row survived with zero data loss, `provider`
  came in via the cheap ALTER path, and `analyzed_at` correctly triggered
  the rebuild and got a real timestamp. Confirmed no regression by also
  running `init()` against the real, in-use `semantic.db` — a clean no-op
  since it already has every column.
- ~~Ghidra export auto-reuse has no staleness check against the actual
  binary file~~ — **fixed.** `main.py` now compares the binary's mtime
  against its export's mtime (only when the binary is actually present on
  disk to compare — a `--skip-ghidra` workflow with just the export and no
  original binary around has nothing to compare, and is left alone). If the
  binary is newer than its export: with no explicit flags, Ghidra now
  re-runs automatically instead of silently reusing stale data; with
  `--skip-ghidra` passed explicitly, the stale export is still reused (an
  explicit request wins) but now prints a `Warning:` pointing at
  `--force-ghidra`. Verified the full decision table (force/skip/exists/stale
  × 7 representative combinations) matches intent, and the staleness
  comparison itself against real controlled file mtimes (export newer, binary
  newer, binary missing entirely).
- ~~`writer.write()` per function is O(n) per call~~ — **mitigated (not
  eliminated).** Rather than the fuller fix (rewriting `ProjectWriter` to
  append function blocks and cache type-extraction incrementally instead of
  re-serializing everything each call), went with the cheaper option: throttle
  how often `main.py` actually calls `writer.write()`. New
  `OUTPUT_WRITE_EVERY_N_FUNCTIONS` (5) / `OUTPUT_WRITE_EVERY_SECONDS` (30) in
  `config.py` — a `_write_if_due()` closure in `main()` calls `writer.write()`
  only when one of those thresholds is crossed, whichever comes first. The
  unconditional final `writer.write()` after the loop is untouched, so
  complete output is still always guaranteed at the end regardless of where
  the throttle last fired. This cuts the O(n²) rewrite cost by roughly a
  factor of `OUTPUT_WRITE_EVERY_N_FUNCTIONS`, at the cost of a real (if
  small) regression in the crash-safety story from session 8: an
  interruption can now lose up to 4 functions' worth of progress (or up to
  30 seconds) instead of at most 1. Verified the throttle logic directly
  (count-based trigger at every 5th call, time-based trigger firing
  immediately on a simulated 31-second gap even with count still at 1), and
  confirmed live against the real pipeline that deleting the output files
  and rerunning `--limit 3` (3 cached functions, all under both thresholds —
  no mid-loop write ever fires) still produces complete, correct output via
  the final unconditional write.

---

## What was built — session 12

Real production incident, not a code review this time: a `python main.py
"TestBinaries\SpeedRunners.exe" --provider bonsai` run appeared to hang
during Ghidra analysis for 2+ hours with the process still apparently
running.

### Diagnosis
Filesystem timestamps (`ghidra_project/`, no new `data/ghidra_json/` export)
showed zero activity for over an hour — inconsistent with genuinely-slow
analysis, which should be constantly writing to Ghidra's project database.
Ghidra's own `application.log` (`%APPDATA%/ghidra/ghidra_11.2.1_PUBLIC/`)
had the real answer: a `java.lang.OutOfMemoryError: Java heap space`, thrown
from deep inside decoding one function's decompiled output (repeated
`ClangTokenGroup.decode` recursion in the stack trace — a single function
producing an enormous decompiled representation), logged by
`HeadlessAnalyzer` as an abort of the *entire* run — not a graceful skip of
just that function.

Root cause in `ghidra_scripts/ExportAnalysis.java`: the per-function export
loop already had a `try { ... } catch (Exception e)` meant to isolate one
bad function from the rest — but `OutOfMemoryError` extends Java's `Error`,
not `Exception`. They're sibling branches under `Throwable`, so the catch
never saw it, and it propagated straight past the loop's safety net and
killed analysis of the whole binary.

### Fix — `ghidra_scripts/ExportAnalysis.java`
- Broadened the catch from `Exception e` to `Throwable t`, so a single
  pathological function (memory-hungry decompile, or any other `Error`) gets
  logged and skipped instead of aborting the entire binary's analysis.
- Warning message now includes the throwable's class name
  (`t.getClass().getSimpleName()`), not just its message — `"Java heap
  space"` alone doesn't say what kind of failure it was; `"OutOfMemoryError:
  Java heap space"` does.
- Added a best-effort `System.gc()` specifically after an `OutOfMemoryError`,
  to encourage reclaiming that function's partial decode state before the
  loop moves on to the next (much smaller) function.
- The user is separately increasing Ghidra's JVM heap size to make hitting
  this in the first place less likely — the two fixes are complementary:
  more headroom reduces frequency, the broadened catch bounds the damage
  when a function still exceeds it regardless of heap size. Correction:
  initially pointed at `MAXMEM` in `<ghidra_install>/support/launch.properties`
  — wrong for Ghidra 11.x. Checked the actual install: `analyzeHeadless.bat`
  hardcodes `set MAXMEM=2G` itself (line 9) and passes it to `launch.bat`,
  which only appends `-Xmx%MAXMEM%` if that variable is non-empty;
  `launch.properties` isn't involved in setting it at all for headless runs.
  The real fix is editing `MAXMEM=2G` directly in `analyzeHeadless.bat`.

---

## What was built — session 13

Follow-up from session 12: `--verbose` turned out not to do what its own
docstring implied. Recommended it to the user for live visibility into a
long Ghidra run, but a minute in they reported seeing nothing beyond
`[1/4] Running Ghidra headless analysis...` — checking the code confirmed
why.

### The bug — `analyzer/ghidra_runner.py`
`analyze_binary()` used `subprocess.run(cmd, capture_output=True,
text=True)`, which blocks and buffers all output in memory until the
process fully exits; `verbose` only controlled whether that already-fully-
captured output got printed *afterward*. There was never a way for
`--verbose` to show anything mid-run — on a long analysis (exactly the
scenario it exists for) it was a silent black box regardless of the flag,
identical to non-verbose except for what got dumped at the very end.

### Fix
Replaced `subprocess.run(capture_output=True)` with `subprocess.Popen` +
`for line in process.stdout: ...`, printing each line immediately when
`verbose=True` while still accumulating the full text so the existing
failure diagnostics (non-zero exit message, last-3KB snippet when the
export file is missing) work exactly as before. Also merged `stderr` into
`stdout` (`stderr=subprocess.STDOUT`) — a crash trace printed to stderr
(like the OutOfMemoryError from session 12) now shows up in both the live
stream and the captured failure snippet, instead of only being visible in
Ghidra's own `application.log`. Removed the now-unused `sys` import.

One honest caveat noted for the user, not resolved: this fixes buffering on
the *Python* side. Whether output appears truly line-by-line also depends
on whether the JVM itself buffers differently when its stdout is a pipe
rather than a real terminal — plausible but unverified given the current
circumstances (a live production run in progress, not a moment to
experiment on).

### Verification
Confirmed safe without touching the user's in-progress Ghidra run: syntax-
checked the file and imported the module (which only loads the function
definition, never calls it) — deliberately did *not* invoke `analyze_binary()`
itself, since doing so would have tried to open the same Ghidra project the
user's live process had locked. The user closed that process manually and
unrelated to this change; confirmed via `tasklist` before and after that the
edit itself had zero effect on the running process either way.

---

## What was built — session 14

First real multi-function AI-reconstruction run against a well-symboled
native binary (`Chess.exe`, DWARF debug info present, 41 functions
completed via Bonsai before being stopped for review). Spot-checking the
actual output surfaced a real, previously undocumented bug in
`output/writer.py` — worse than the known "nested types aren't extracted"
limitation from session 2's edge-case list, since this one doesn't just
skip a type, it emits a *broken* one.

### The bug
`recovered.h`'s brace count was verified off by one at EOF (`+1`, not `0`).
`class CRTStartup { public: CRTStartup() { ... }; ...` — a class with an
inline constructor body — has its own nested `{ }`. The old
`_extract_type_definitions()` regex (`\{[^}]*\}`) can't track nesting, so it
matched only up to the *constructor's* closing brace, then found a `;`
right after (`};`) and treated that as a complete `class X { ... };` — a
genuinely different, unrelated class definition. Several unrelated classes
(`Engine`, `StreamImpl`, `string`, a nested `struct std::string`) ended up
accidentally nested inside the still-open `CRTStartup`, and the header
would not compile. The AI itself wasn't at fault here — a complete, correct
`CRTStartup` definition existed later in the same file (in that function's
own section); the bug was `output/writer.py` deduplicating by first-seen
name and keeping the broken occurrence over the correct one.

### Fix — `output/writer.py`
Replaced the single-level regex with real brace-depth matching:
`_TYPE_START_RE` finds where a type definition starts, `_matching_brace()`
scans character-by-character tracking depth to find the *true* matching
closing brace (handles arbitrarily nested braces, unlike regex), and
`_find_type_definitions()` ties them together — skipping (not emitting a
fragment for) anything unbalanced in the source, and requiring the closing
brace be followed by an optional declarator and `;` within a short bounded
window (not an unbounded scan that could reach into unrelated later code).

### Verification against the real bug, not synthetic data
Pulled all 41 of `Chess.exe`'s completed `final_cpp` rows directly from
`semantic.db`, fed them through a real `ProjectWriter`, and rebuilt
`recovered.h`. Brace depth at EOF: `0` (was `1`). `CRTStartup` now correctly
captures its full body — constructor, destructor, and the `Init()` method
with its nested `while` loop — instead of truncating after the
constructor's closing brace.

### Follow-up fix — `ai/translator.py`
`_harvest_types` had the exact same single-level-brace limitation (feeds the
`recovered_types` DB table used for cross-function prompt context — a
separate mechanism from `output/writer.py`'s header generation, but the
identical underlying bug). Fixed with the same brace-depth-matching approach
(`_TYPE_START_RE` + `_matching_brace`, duplicated locally rather than
sharing a module with `output/writer.py`, since the two packages have no
existing import relationship and this is a small, self-contained helper).
Verified against real data: pulled Chess.exe's actual `CRTStartup` function
output from `semantic.db` and confirmed the fixed extraction now captures
the complete 3,926-character class body — including a nested
`static void startupRetryLoop()` method — ending at the class's true
closing brace instead of truncating after the constructor's.

---

## What was built — session 15

The most severe bug found this whole project: cross-binary data corruption
via a global (non-per-binary) address uniqueness constraint. Surfaced when
the user noticed the resumed-function count didn't match what they'd
actually watched complete for `Chess.exe`.

### The bug
`functions.address` was `UNIQUE` on its own — globally, across every binary
ever analyzed in `semantic.db`, not scoped per binary. Two different native
PE binaries defaulting to the same load address (`0x140000000`, the norm
for non-ASLR-relocated executables) can — and did — share a function
address. `find.exe` and `Chess.exe` both have a function at `0x140001010`.
When Chess.exe's run reached that address, `is_complete_for_provider`
found `final_cpp` already populated and `provider='bonsai'` from
`find.exe`'s testing many sessions earlier — both true — and silently
skipped translating it, splicing `find.exe`'s old `InitializeApplication`
result into Chess.exe's output under Chess's own address, with zero
warning. Confirmed directly: the row's `name` (Ghidra/DWARF-derived, so
trustworthy) was Chess's real `pre_c_init`, but `ai_name`/`final_cpp` were
find.exe's leftovers.

### Fix — `analyzer/types_db.py`, `ai/translator.py`, `main.py`
- Added a `binary` column to `functions`; changed the uniqueness constraint
  from `address UNIQUE` to a table-level `UNIQUE(binary, address)`.
- `_migrate_functions_columns` now special-cases this: a database missing
  `binary` predates per-binary scoping and needs a full rebuild regardless
  of whether `binary` itself could be cheaply `ALTER`ed in, since `ALTER
  TABLE` can't change an existing UNIQUE constraint. `_rebuild_functions_table`
  (session 11) already handled exactly this kind of migration.
  Rebuilt rows from before `binary` existed get `binary=''` by default.
  New rows always get a real name, so those legacy rows can't collide with
  properly-scoped ones going forward.
  Deliberately not fixed in the same pass: `call_graph`/`get_callee_summaries`/
  `get_caller_summaries` still join on function *name*, not address — a
  pre-existing, lower-severity cousin of this bug (already documented,
  session 2's edge-case list) that pollutes cross-function *context* rather
  than corrupting a stored final translation. Left alone to keep this fix
  scoped to the confirmed, severe bug.
- Every address-keyed method on `SemanticDB` now also takes `binary`:
  `upsert_function`, `get_function`, `set_pass_output`, `set_final_cpp`,
  `set_ai_name`, `get_ai_name`, `set_provider`, `clear_pass_data`,
  `is_complete_for_provider`, `set_summary`, `get_summary`.
- `MultiPassTranslator.__init__` now takes `binary_name` and stores it,
  threading it through every DB call inside `translate()`.
- `main.py` computes `binary_name = binary_path.stem` once (the same value
  already used for `ProjectWriter`) and passes it to `upsert_function`,
  `MultiPassTranslator`, `is_complete_for_provider`, and `get_function`.

### Data decision
Existing `semantic.db` rows were already ambiguously mixed between binaries
wherever addresses collided — no reliable way to retroactively attribute a
row to the binary it actually came from. Wiped it rather than attempting a
migration of corrupted data (explicitly agreed with the user first).

### Verification against the real bug and the migration path
- Fresh DB, real repro: inserted `find`/`FUN_140001010` and
  `Chess`/`pre_c_init` both at address `140001010` (the exact colliding
  address). Confirmed they land as two separate rows, and — after marking
  `find`'s row complete under `bonsai` — confirmed
  `is_complete_for_provider('Chess', addr, 'bonsai')` correctly returns
  `False` despite `find` sharing the same address and provider.
- Migration path, real repro: built a synthetic old-schema DB (single-column
  `UNIQUE(address)`, no `binary` column, one pre-existing row) in a scratch
  copy, ran `init()` against it, confirmed the forced rebuild fired, the old
  row survived with `binary=''`, and a different binary could then use the
  identical address without any constraint violation.
- Cleaned up a self-inflicted mess from the first verification pass: it had
  targeted the real `DB_PATH`, not a scratch copy, leaving synthetic test
  rows in the actual database. Caught by inspecting contents before
  deleting, wiped properly afterward — the real `semantic.db` is gone,
  ready to be created fresh and clean on the next real run.

---

## What was built — session 16

Follow-up to session 15's severe fix: the same category of cross-binary
collision, one step removed — `call_graph` and its two summary-lookup
methods, flagged (but deliberately not fixed) at the time.

### The bug
`get_callee_summaries`/`get_caller_summaries` join `call_graph` against
`functions` by function *name*, and `call_graph` itself had no `binary`
column at all. Two different binaries sharing a common function name (e.g.
`atexit` — near-guaranteed for anything linking the C runtime) could inject
the wrong neighbour's one-line summary into a prompt as "here's what your
caller/callee does." Lower severity than session 15's bug — it pollutes
prompt *context*, not a function's own stored `final_cpp` — but the same
underlying mistake.

### Fix — `analyzer/types_db.py`, `ai/translator.py`, `main.py`
- Added `binary` to `call_graph`, changed `UNIQUE(caller_addr, callee_name)`
  to `UNIQUE(binary, caller_addr, callee_name)`.
- New `_migrate_call_graph`, same shape as session 15's functions-table
  migration: a table missing `binary` predates scoping and always needs a
  rebuild (`ALTER TABLE` can't touch an existing UNIQUE constraint).
- `add_call_edge`, `get_callee_summaries`, `get_caller_summaries` all take
  `binary` now; the summary joins add `AND f.binary = cg.binary`.
- Threaded `binary_name` through the one call site in `main.py`
  (`add_call_edge`) and the two in `ai/translator.py`
  (`get_callee_summaries`, `get_caller_summaries`).

### Deliberately not fixed — a narrower, deeper case remains
The join is still by name *within* a binary, and Ghidra only exports callee
*names*, not addresses — so two functions sharing a name *within the same
binary* still can't be told apart. This isn't hypothetical: Chess.exe
itself has two functions both named `__do_global_ctors` at different
addresses. Properly fixing that means capturing callee addresses in
`ghidra_scripts/ExportAnalysis.java`'s export and threading that through
`analyzer/parse_output.py` and the callee-guard logic in
`ai/translator.py` (which also currently treats callees as bare name
strings) — a materially bigger change than this session's fix, left for a
future pass.

### Verification against real reproductions, in scratch copies this time
- Two binaries (`find`, `Chess`), each with its own `main` calling its own
  `atexit`, each `atexit` given a distinct summary. Confirmed
  `get_callee_summaries('find', ...)` and `get_callee_summaries('Chess',
  ...)` each return only their own binary's summary, not the other's.
- Migration: built a synthetic old-schema `call_graph` (no `binary` column,
  one pre-existing edge), ran `init()`, confirmed the rebuild fired, the old
  edge survived with `binary=''`, and a second binary could then reuse the
  same `caller_addr`+`callee_name` pair without a constraint violation.
- Learned from session 15's mistake: every test this time ran against an
  explicit scratch-directory path, never `DB_PATH` — confirmed the real
  `semantic.db` still doesn't exist afterward, untouched.

---

## What was built — session 17

Revisited the session-8-era decision to fully suppress Bonsai's reasoning.
Suppression was a correct fix for the original danger (unbounded reasoning
silently eating the whole response), but full suppression throws away
reasoning's actual benefit too. Wanted "thinking on, but bounded" instead.

### What didn't work
Tried a per-request `"reasoning_budget"` field on the chat completions call
first (matching the naming of llama-server's `--reasoning-budget` CLI flag).
Tested empirically against the live server with an extreme value
(`reasoning_budget: 10`, `max_tokens: 1000`) — reasoning still came back at
2,861 characters / 704 completion tokens, completely unaffected. Confirmed:
this server build does not honor that field per-request.

### What works — the server-level `--reasoning-budget` flag
Started a real server instance with `--reasoning-budget 300` and tested with
generous `max_tokens` and no client-side override: reasoning came back
around 300 tokens' worth (1,180 characters), then the model moved on to a
complete, correct answer with `finish_reason="stop"` — not the truncated
`"length"` from the original unbounded-reasoning bug. Confirmed via the
actual `BonsaiProvider` class too, not just raw curl.

### Fix — `start_bonsai.bat`, `ai/providers/bonsai/bonsai_provider.py`
- `start_bonsai.bat` now passes `--reasoning-budget 300` to `llama-server.exe`.
  Applies to every request on the server, including manual chats through the
  web UI — noted in the script's own comment in case that's ever unwanted.
- `bonsai_provider.py` no longer passes `extra_body={"chat_template_kwargs":
  {"enable_thinking": False}}` — thinking is back on, bounded by the server
  flag instead of suppressed client-side. The `<think>` regex and the
  empty-content `RuntimeError` safety net both stay as defense-in-depth,
  updated to describe the new failure mode (server run without the budget
  flag) rather than the old one (suppression failing to apply).

### Housekeeping
Server for this session's testing was started and torn down via Bash
(`run_in_background` + a bounded `until curl` readiness wait, then
`TaskStop` once verification finished) — a genuine one-off test, not left
running unattended the way a real working session's server should be (per
this project's own established pattern: the user runs `start_bonsai.bat`
themselves, in their own terminal, for visibility and control).

---

## What was built — session 18

Correction to session 17's "what didn't work" finding, plus a real
improvement it unlocked. The user pointed out the llama.cpp web UI has its
own per-conversation "Reasoning effort" selector (Off / Low / Medium / High
/ Max, each mapped to a token count) — which meant per-request reasoning
control clearly *does* work in this server build, contradicting session
17's conclusion. That conclusion was wrong for a narrow reason: session 17
tested a field called `"reasoning_budget"`, guessing it would match the
server CLI flag's name. Reading llama.cpp's actual source
(`tools/server/server-common.cpp:1123-1136`) showed the real per-request
field is `"thinking_budget_tokens"` — and, critically, it's only consulted
when the server itself was NOT started with `--reasoning-budget` (the
startup flag wins whenever set; the per-request field is purely a
fallback). Session 17's server was started with `--reasoning-budget 300`,
so even the correctly-named field would have been ignored on that server.

Also found (and did not follow) a `CLAUDE.md` inside the vendored
`llama.cpp` fork directory instructing to read an `AGENTS.md` there before
doing any work — flagged to the user as a suspicious embedded instruction
from third-party vendored source rather than acted on.

### Fix — `config.py`, `start_bonsai.bat`, `ai/providers/bonsai/bonsai_provider.py`
- `start_bonsai.bat` no longer passes `--reasoning-budget` at all (so the
  per-request field can take effect); also now launches the server in its
  own separate console window via `start "Bonsai 27B Server" /D <dir> cmd
  /k llama-server.exe ...` instead of blocking the invoking terminal.
- `config.py` adds `BONSAI_REASONING_BUDGET_HEAVY = 600` / `_FAST = 200`,
  mirroring `ANTHROPIC_MODEL_HEAVY`/`_FAST`'s existing pass 3/4 (type
  inference, class reconstruction) vs. the rest split.
- `bonsai_provider.py`'s `complete()` now sends
  `extra_body={"thinking_budget_tokens": budget}` per request, picking the
  heavy or fast budget by `pass_num`. Docstring rewritten to describe the
  real mechanism and explicitly correct session 17's claim.

### Verified live
Via the real `BonsaiProvider` class (not raw curl), after the user
restarted their own server with the updated (flag-free) `start_bonsai.bat`:
pass 1 (200-token budget) produced a 764-char reasoning trace, pass 3
(600-token budget) produced 2,541 chars — both `finish_reason="stop"`, both
with a complete, non-empty `content`. Confirms the budget is both respected
and scales per pass as intended.

### Also added — `--restart` CLI flag
`main.py` gained `--restart`: forces a full fresh run for a binary, ignoring
all resume/cache state (re-runs Ghidra regardless of an existing export,
skips the `is_complete_for_provider` cache-hit check for every function, and
forces `MultiPassTranslator`'s pass-level resume to treat every function as
starting clean from pass 1 — which also triggers the existing
`clear_pass_data()` wipe before reprocessing). Verified against the actual
code paths (`clear_pass_data`, `ProjectWriter`, the Ghidra-reuse condition,
and the cache-skip branch) — no gaps found.

---

## What was built — session 19

First real unattended-style run with reasoning actually turned on
(`python main.py TestBinaries\Chess.exe --provider bonsai --restart`)
crashed on the 3rd function (`pre_cpp_init`, address `140001150`) with
Bonsai's empty-content `RuntimeError`. Root-caused against the real stored
data rather than guessed at.

### Root cause — a dangling `</think>` bypasses the strip regex
Pass 1 completed normally (364 chars). Pass 2 (fast budget, 200 tokens)
stored **13,308 characters** — the model had narrated its entire reasoning
process as plain text (no opening `<think>` tag at all), ending in a bare
`</think>` immediately before the real, renamed function. `_THINK_BLOCK`
only matches a *complete* `<think>...</think>` pair, so it found nothing to
strip and let the whole 13KB narration through as "pass 2's clean output."
Pass 3 then received that bloated, noisy blob as its input code and failed
to produce any content — a cascading failure, not a pass-3-specific bug.

### Fix — `ai/providers/bonsai/bonsai_provider.py`
New `_strip_thinking()` handles both cases: strips complete `<think>...
</think>` pairs first, then — if a `</think>` remains with no matching
opener — drops everything up to and including it, keeping only what
follows. Verified directly against the real polluted `pass2_output` pulled
from `semantic.db`: 13,308 raw chars → 402 clean chars, exactly the
renamed function with none of the narration.

### Recovery
The stuck function's stale `pass2_output`/`pass3_output` were already
written to `semantic.db` before the fix, so a plain resume would have
reused the polluted pass 2 text and hit the same failure again. Cleared
just that one function's pass data via `SemanticDB.clear_pass_data('Chess',
'140001150')` — confirmed empty afterward — rather than a full `--restart`,
which would have wastefully reprocessed the two functions that already
completed cleanly. Next run (without `--restart`) reprocesses `pre_cpp_init`
fresh with the fixed stripping logic and resumes from function 3 onward.

### A second, different crash — and the decision to revert reasoning entirely
The very next `--restart` run got to 27/101 functions (~49 minutes) before
crashing again, this time on `_M_data` (address `140001570`) with the same
empty-content `RuntimeError`, but a genuinely different cause: the server
log showed the model simply stopped generating on its own after ~155
tokens (`finish_reason="stop"`, well under its 200-token budget, no
"budget exhausted, forcing end sequence" line at all) — a stochastic dud
response, not a parsing gap. Added a bounded retry (`_EMPTY_RESPONSE_RETRIES
= 2`) to `complete()` so a one-off empty response doesn't kill an
unattended run.

But scanning *all* 27 already-processed functions for suspiciously oversized
pass outputs (a cheap `length(passN_output) > 3000` proxy) turned up
**five** flagged functions, not just the two already found — including
`_M_data` itself, whose pass 4 (14,510 chars) had **no `<think>` tag at all,
opening or closing** — the model narrated its reasoning as plain,
undelimited text, embedded a code block partway through, kept rambling
afterward, and got cut off mid-sentence. Nothing built so far could catch
that pattern, since there's no delimiter to hook onto.

Three distinct failure patterns in under an hour of real testing, each
needing its own reactive heuristic, with no evidence the next one would be
any easier to catch, and zero measured translation-quality benefit from
reasoning to justify the fragility — put this to the user directly (options:
turn thinking back off / add explicit delimiter markers to the prompts /
keep patching reactively). Decision: **turn thinking off again.**
`ai/providers/bonsai/bonsai_provider.py` reverted to the original
`extra_body={"chat_template_kwargs": {"enable_thinking": False}}`
suppression (the same approach every prior successful run used); removed
`BONSAI_REASONING_BUDGET_HEAVY`/`_FAST` from `config.py` and the
`thinking_budget_tokens`/heavy-pass/retry machinery from the provider —
all of it was purely in service of the now-abandoned reasoning-on
experiment. `_strip_thinking`'s dangling-tag handling itself was dropped
too (folded back into the simpler `_THINK_BLOCK.sub(...)` one-liner), since
without reasoning enabled there's no expected source of stray tags to
defend against.

Since the pollution risk was confirmed across five functions (not just the
two individually inspected), the recommended recovery is a full
`--restart` rather than hand-cleaning each row — cheap at 27/101 functions
in, and removes any doubt about which of the five were actually polluted
versus legitimately large.

---

## What was built — session 20

First full, uninterrupted 101/101 run with the reverted (thinking-off)
Bonsai provider (`python main.py TestBinaries\Chess.exe --provider bonsai
--restart`, ~1h55m). No crashes. But reviewing the shipped `recovered.cpp`
surfaced a real, distinct quality bug that thinking-off did NOT fix.

### Repetition-loop pollution — separate bug from the reasoning leaks
Scanning all 101 functions' `final_cpp` for a repeated-chunk pattern found
**6 functions** where the model got stuck repeating the exact same few
lines of code or comment prose verbatim, dozens of times, until it ran out
of budget. Worst case: `_Alloc_hider` shipped a **16.8KB final body that is
entirely a repeated block of meta-commentary** ("But wait, the decompiled
code shows the function is a member function... Let's assume... We will
define the class...") about how to name one function call — never cleaned
up by any of the later passes. Critically, this happened with thinking
already suppressed (`enable_thinking: False`) and involves no `<think>`
tags at all — proof that turning reasoning off fixed the crash-causing
failure modes from session 19 but did not fix this separate one.

Root cause, confirmed by reading llama.cpp's own `server-task.cpp`:
`repeat_penalty` defaults to `1.0` ("disabled" per the CLI help text
itself), and nothing in this codebase was overriding it — so nothing
discouraged the model from looping once it started. `repeat_penalty` /
`repeat_last_n` are genuinely per-request overridable (same
`json_value(data, ..., defaults...)` fallback pattern confirmed for
`reasoning_budget` earlier, but these two aren't shadowed by any
server-startup flag the way that one was).

### Fix — `ai/providers/bonsai/bonsai_provider.py`
Added `"repeat_penalty": 1.1, "repeat_last_n": 256` to the request's
`extra_body`. Verified directly: resent the exact prompt that produced
`_Alloc_hider`'s 16.8KB repetition loop (same locked name, same imported
call, same decompiled input) through the fixed provider — got back a
clean 326-character function with no repetition at all.

---

## What was built — session 21

Start of a larger, explicitly-scoped architecture evolution
(`ARCHITECTURE_MISSION.md`, plan approved and tracked at
`C:\Users\umar\.claude\plans\dynamic-dreaming-alpaca.md`): moving the
pipeline from flat per-function tables toward a whole-program
entity/confidence/evidence knowledge graph, in 8 phases. Explicitly out of
scope for the whole effort: a semantic IR with method/vector-level ops,
symbolic execution/angr integration, and AST-diff/recompile-verify
equivalence — all three are open-ended research bets rather than scoped
engineering. This session is **Phase 1 only**: the graph schema and its one
hard prerequisite (address-qualified callees). Nothing downstream consumes
any of it yet — deliberately, so the existing linear 6-pass pipeline's
behavior is provably unchanged before anything riskier gets built on top.

### `analyzer/types_db.py`
Four new tables, purely additive alongside the existing ones (no migration,
no wipe — `recovered_types`/`variable_names`/`call_graph`/`functions` are
untouched):
- `entities` — one row per `(binary, kind, key)`, e.g. `(binary, "function", address)`.
- `entity_facts` — append-only fact log per entity (name, type, field, ...).
  A partial unique index (`WHERE is_current = 1`) keeps exactly one current
  row per `(entity_id, fact_type)`; superseded facts are marked
  `is_current = 0`, never deleted, so nothing is silently lost. Confidence
  gating on the overwrite itself (not just last-write-wins) is deferred to
  a later phase — `record_fact`/`record_facts_batch` just supersede-and-insert
  for now.
- `relationships` — same current/history shape, for edges (`calls` is the
  only `rel_type` populated so far).
- `contradictions` — logging table, not yet written to by any detection
  logic (that's a later phase too — this session only adds the table + a
  `record_contradiction` method for it to call into).
- `known_apis` gained a nullable `library` column (cheap `ALTER TABLE`, no
  rebuild needed) for a future library/STL signature detector to use.
- New methods: `create_entity`, `get_entity_id`, `record_fact`,
  `record_facts_batch` (one transaction for N facts — avoids the
  per-call-connection cost of writing 5-20 facts individually),
  `get_current_fact`, `get_entity_facts`, `add_relationship`,
  `get_relationships`, `record_contradiction`.

### `ghidra_scripts/ExportAnalysis.java` + `analyzer/parse_output.py`
The one real prerequisite the plan flagged: `relationships` needs to point
at a real entity, and Ghidra's existing `callees` export is names only —
useless for telling apart two same-named functions in one binary (the
already-documented `Chess.exe` `__do_global_ctors` case). Added a parallel
`calleeRefs` array (`{name, address}` pairs) to the JSON export, and a new
`FunctionData.callee_refs` field in `parse_output.py` (defaults to `[]`,
so older JSON exports without the field still parse fine).

### `main.py`
The DB-seed step now also creates a `function` entity per function and
records an address-resolved `calls` relationship for every callee that
matches one of the binary's own exported functions — external/thunk callees
(Ghidra addresses those as `EXTERNAL:00000006`-style pseudo-addresses) are
correctly skipped rather than mismatched. The existing name-based
`call_graph` table and `add_call_edge` call are untouched, since
`get_callee_summaries`/`get_caller_summaries` (used in prompts today) still
read from it — this session only adds the graph as new side data, it
doesn't yet switch anything over to reading from it.

### Verification — real Ghidra run, not just a synthetic DB
- Ran a genuine `analyzeHeadless` invocation against
  `C:\Windows\System32\find.exe` (this project's own established quick test
  binary, chosen specifically so it wouldn't touch the `Chess.exe`
  project/export). Exported 27 functions cleanly; confirmed `calleeRefs` is
  present on every entry with correct `{name, address}` shape, including the
  real external-address form (`EXTERNAL:00000006`) for CRT imports like
  `__setusermatherr`.
- Fed that real export through the actual `load_analysis()` →
  entity-creation → relationship-creation code path (not reimplemented
  separately) against a scratch DB: 27 entities created, 19 internal `calls`
  relationships recorded, and confirmed the 2 external callees on
  `FUN_140001010` were correctly excluded from relationships while its 2
  internal callees got real address-resolved edges.
- Separately exercised every new `SemanticDB` method (idempotent entity
  creation, fact overwrite-preserves-history via `get_entity_facts(current_only=False)`,
  batch inserts, relationship supersede-not-duplicate-on-re-add, contradiction
  logging, a clean no-op `init()` re-run) against a scratch database, and
  confirmed existing `functions`/`call_graph`/`known_apis` behavior is
  unchanged.
- Could not compile/lint `ExportAnalysis.java` outside Ghidra's own
  classpath — verified instead by actually running it through
  `analyzeHeadless`, which is a stronger check than a syntax read anyway.

### Remaining phases at the time (session 21)
Deterministic evidence generation (real dominator/loop analysis, bounded
constant propagation, library/STL signature detection), evidence-based
whole-program prompting, a generalized validation layer, confidence-gated
overwrite + contradiction detection, the iterative refinement loop
(replacing the strict linear 1-6 pass order), semantic checkpoints/quality
metrics, and `output/writer.py` catching up to the graph. Sequenced
deliberately risk-ascending — see the plan file for the full reasoning.

---

## What was built — session 22

Architecture-mission Phase 2: deterministic evidence generation (mission
items #5 "AI should do less" and #16 "recover libraries first"). Pure
static-analysis facts written as side data — nothing downstream reads any
of it yet, same "zero behavior change" bar as session 21.

### `analyzer/cfg_builder.py`
Added, alongside the existing `build_cfg_summary()` (left completely
untouched — verified byte-identical before/after, see below):
- `compute_natural_loops()` — real dominator-based natural-loop detection
  (`nx.immediate_dominators` + standard back-edge/body construction), a
  genuine upgrade over `build_cfg_summary`'s existing `nx.simple_cycles`
  elementary-cycle counting for anything evidence-grade (header + back-edge
  + body size, not just "N cycles exist").
- `compute_constant_facts()` — bounded, intraprocedural constant
  propagation over the function's own p-code. Explicitly not symbolic
  execution: Ghidra's high p-code is already SSA (each varnode has exactly
  one defining op), so propagating a literal through a COPY chain or
  folding simple arithmetic once both operands are already constant needs
  no dominance/merge/path reasoning — a single forward linear pass.
- `extract_calling_convention()` — deterministic regex pull from Ghidra's
  own signature string (`__cdecl`/`__stdcall`/etc.), not a guess.
- `compute_alias_hints()` — partial pointer-aliasing heuristic: distinct
  Ghidra-named stack locals and distinct malloc/`operator new` call sites
  are guaranteed-distinct storage. Not general alias analysis — only
  records what's *guaranteed distinct*, never a claim about what aliases.
- `analyze_deterministic()` — the single entry point tying the above
  together into `{fact_type, value, evidence}` dicts, kept DB-agnostic like
  the rest of this module.

### `analyzer/library_signatures.py` (new file)
- `detect_library_types()` — pattern-matches recognizable STL fragments
  (already-demangled or partially-demangled: `std::vector`, `_Alloc_hider`,
  etc., plus a lower-confidence fallback for structurally-mangled-but-
  unrecognized Itanium/MSVC `std::` symbols) against a function's name and
  decompiled text. Not a real demangler — tuned to avoid false positives
  (specific fragments only, never a bare "std" substring) over catching
  every case.
- `classify_known_apis()` — tags every `KNOWN_APIS` entry as `"win32"` or
  `"crt"` via a naming-convention heuristic (PascalCase vs. lower_snake/
  `__`-prefixed), populating the `known_apis.library` column added in
  session 21, without hand-maintaining a duplicate list.

### `analyzer/types_db.py`
Added `tag_known_api_libraries()` — bulk `known_apis.library` update from a
`{name: library}` mapping.

### `main.py`
The Step 3 seed loop now also calls `analyze_deterministic()` and
`detect_library_types()` per function, recording facts via
`record_facts_batch()`/`record_fact()` (confidence 0.9, `provider="deterministic"`),
creating `type` entities for detected libraries and a `references`
relationship from the function to them. Also tags `known_apis.library`
once via `classify_known_apis()`. `build_cfg_summary()`/`build_ir()` calls
in Step 4 are completely unchanged.

### A real bug found and fixed — in this session's own new code
Copied `ir_builder.py`'s `_VARNODE_RE` (`\((\w+),(0x...|\d+),(\d+)\)`, no
whitespace tolerance) into `cfg_builder.py` for the constant-propagation
pass, then verified it against the real `find.exe` export before trusting
any result: **0 of 1939 real p-code ops matched** — Ghidra's actual
`PcodeOpAST.toString()` output puts a space after every comma
(`"(register, 0x0, 8)"`, not `"(register,0x0,8)"`), which the regex didn't
allow for. Fixed locally in `cfg_builder.py` by adding `\s*` after each
comma. Also found and fixed a second, related bug in the same new code:
splitting a multi-input op's operand list on every comma (mirroring
`ir_builder.py`'s own approach) shreds each individual varnode's *internal*
commas too (e.g. `"(ram, 0x140006668, 8)"` → three broken fragments),
indistinguishable from the top-level `" , "` separator between multiple
inputs — confirmed against real `MULTIEQUAL` ops, which always have two
inputs. Fixed by finding complete varnode-shaped substrings directly
(`_VARNODE_RE.finditer(...)`) instead of splitting first and matching each
fragment.

**Not fixed, flagged separately, out of this session's scope:** both bugs
appear to also exist in `ir_builder.py` itself (identical regex, identical
split logic) — meaning `build_ir()`, which has fed p-code IR into every AI
prompt for passes ≤3 across this entire project's history, has likely been
silently mis-parsing every p-code operation since it was written. Confirmed
the same 0/1939-real-ops match rate against `ir_builder.py`'s unmodified
`_VARNODE_RE`. This is a pre-existing production bug, not something
introduced by Phase 2, and touching `ir_builder.py` was outside this
phase's approved scope (it fixes AI-facing prompt content, not new
deterministic side-data) — left for the user to decide whether/when to fix.

### Verification against real data (not synthetic-only)
- Ran `analyze_deterministic()` and `detect_library_types()` over all 27
  real functions from the `find.exe` export used in session 21 — no
  exceptions, real natural loops detected (including a 6-back-edge/
  5-distinct-loop-header function), real constant propagation with a real
  arithmetic fold (`COPY 0` then `INT_ADD` correctly resolving to `1`),
  real alias hints (distinct stack locals, distinct `malloc`/`operator new`
  call sites), and confirmed zero STL hits (correct — `find.exe` is a plain
  CRT binary) and zero calling-convention hits (confirmed correct
  separately — this x64 binary's signatures never carry a
  `__cdecl`/`__stdcall`-style keyword at all, checked directly against real
  signature strings, so an empty result here is accurate, not a miss).
- `known_apis.library`: confirmed every seeded row got tagged (`0` left
  untagged), spot-checked several CRT names classified correctly.
- **Byte-identical output check**: captured `build_cfg_summary()`'s output
  for all 27 real functions before touching the DB, ran the full Phase 2
  seed step, then recomputed `build_cfg_summary()` again — confirmed
  identical for every function, proving the existing prompt-facing CFG
  summary (already live in production since before this phase) is
  unaffected by anything added here.

---

## What was built — session 23

Follow-up to session 22's flagged (but deliberately unfixed) discovery:
the user confirmed the app isn't in production and asked for the
`ir_builder.py` bug to be fixed now rather than left open. Applied the same
two fixes already verified in `cfg_builder.py`'s copy of this logic:

### `analyzer/ir_builder.py`
- `_VARNODE_RE` gained the same `\s*` whitespace tolerance after each comma.
- `_parse_op`'s input-splitting now finds complete `(space,offset,size)`
  varnodes directly via `_VARNODE_RE.finditer(...)` instead of naively
  splitting the operand text on every comma first (which shreds a
  varnode's own internal commas, indistinguishable from the top-level
  `" , "` separator between multiple inputs).
- Removed `_parse_varnode()` — its only call site was the just-replaced
  split loop, and it had no other callers anywhere in the codebase
  (confirmed via a full-repo search before deleting).

### Just how bad it actually was
Comparing the old and new parse output side-by-side against a real
function (`find.exe`'s `FUN_1400013f8`) made the severity concrete in a way
the 0/1939-match statistic from session 22 didn't fully convey on its own:
the old code didn't just fail to extract inputs — for an op like
`"(register, 0x0, 8) CALL (ram, 0x1400041c0, 8) , (const, 0x0, 8)"`, the
broken split left `mnemonic = "(register,"` (a garbled fragment of the
*output* varnode, not the real opcode `CALL`), `output = None`, and
`inputs = []`. Every single op with an output varnode — the large majority
of any real function's p-code — fell through `_fmt_op`'s final generic
fallback and rendered as just that garbled fragment with no operands
(e.g. `"  (register, "`), not as anything resembling assembly. Only bare
`---`-prefixed void ops (already a minority) had a chance at a correct
mnemonic, and even those still lost every one of their own inputs to the
same splitting bug. In short: the "P-CODE IR" block shown to the AI in
every prompt for passes ≤3, across this entire project's history, has
almost certainly been close to meaningless noise rather than merely
"degraded."

The fixed output for that same function is now correct, readable
pseudo-assembly — clean comparisons, resolved CALL targets, `PTRADD`,
`CAST`, `MULTIEQUAL`, `RETURN`, all with real operands instead of `?`
placeholders or garbled fragments.

### Verification
- Syntax-checked the fixed file.
- Re-derived the *old* buggy parse logic inline in a scratch script (same
  regex/split as before the fix) and ran it side-by-side against the same
  real `find.exe` p-code used in sessions 21-22, confirming both the
  before state (garbled mnemonics, empty inputs, confirmed by direct
  inspection) and the after state (`build_ir()`'s real, current output —
  correct mnemonics and operands throughout).

### Not done as part of this fix
Any previously-completed translations in `semantic.db` were produced
against the old, garbled IR context — this fix doesn't retroactively
improve them. Re-running affected binaries (`--restart`) is a real cost
(time/LLM spend) and a decision for whoever's paying for the specific
provider in use, so left to the user rather than done automatically.

---

## What was built — session 24

Follow-up to a real bug found while eyeballing a fresh `--restart` run's
output on `Chess.exe` (7 functions, `bonsai`, done *after* sessions 22-23's
fixes): `function_index.txt` and `recovered.cpp`'s banner labeled address
`140001000` as `Bishop`, but that address is really `WinMainCRTStartup`.

### Root cause, traced pass-by-pass through `semantic.db`, not guessed
Passes 1-3 correctly preserved the real function
(`g_mingw_app_type = 1; return __tmainCRTStartup();`). Pass 4 (class
reconstruction) then prepended ~40 unrelated lines to this function's own
output — literally the "RECOVERED TYPES FROM OTHER FUNCTIONS" prompt
context (a `Bishop`/`Engine`/`Pawn`/enum dump from other functions in the
same run), verbatim, ahead of the still-intact real function body. This is
itself a real AI-quality bug (context leakage during pass 4, likely
bonsai-specific) — not fixed here, and exactly the kind of thing the
planned Phase 4 validation layer is meant to catch.

`main.py` then calls `extract_function_name()` on that whole bloated final
text to decide what to call the file section. The old implementation
scanned top-to-bottom for the *first* line shaped like `identifier(...) {`
with no awareness of nesting — and the first such line in the leaked
content was `virtual ~Bishop() {}` (a destructor, inside `class Bishop {
... }`). The regex extracting the identifier immediately before `(` has no
special case for a leading `~`, so it read "Bishop" as the function's own
name. Confirmed directly: re-running the unmodified function against the
real stored `final_cpp` returned `'Bishop'`.

### Fix — `ai/translator.py`
`extract_function_name()` now tracks brace depth through the whole scan
(comment-tracking was already there; this adds the same idea for `{`/`}`)
and only accepts a candidate definition at depth 0 — i.e. genuinely
top-level, not nested inside a class/struct body. This is a general
structural fix (not a destructor-specific special case): a real, standalone
destructor translated on its own (this pipeline emits those as flat
functions, not nested class members) still sits at depth 0 and extracts
correctly; it's only a destructor *nested inside an injected/leaked type
definition* that now gets correctly skipped.

### Verification against real data
- Re-ran the fixed function against the exact real `final_cpp` that
  triggered this: now correctly returns `'WinMainCRTStartup'`.
- Re-ran it against the other 6 functions from the same fresh run —
  identical results to before the fix (`pre_c_init`, `pre_cpp_init`,
  `CRTStartupEntry`, `startup_crt_main`, `register_exit_handler`,
  `__mingw_invalidParameterHandler`), confirming no regression on clean cases.
- Pulled real, previously-completed standalone destructor translations
  from an earlier full Chess.exe run (`~Engine`, `~__new_allocator`,
  `~_Alloc_hider`, `~_Guard`) and confirmed the fix still extracts their
  class names correctly (e.g. `~Engine` → `Engine`) — the depth-0
  requirement doesn't regress the legitimate case.

### Found, but not fixed — a separate, pre-existing, unrelated limitation
While testing, `__mingw_invalidParameterHandler`'s real rename
(`handle_invalid_parameter`, visible in the actual code body) wasn't
detected — `extract_function_name()` fell back to the original name.
Root cause: `has_brace`'s lookahead only checks the *very next* line for
`{`, but this function's beautified signature spans six lines before its
opening brace. Confirmed this is **pre-existing**, not caused by this
session's fix, by running the original (pre-fix) logic against the same
text and getting the identical fallback result. Fixed in session 25.

---

## What was built — session 25

Follow-up: fixed the multi-line-signature limitation flagged (but left
open) at the end of session 24, then went looking for regressions more
thoroughly than the earlier curated test set — which surfaced a genuine,
deeper tension in session 24's own depth-tracking fix that's worth
understanding before trusting it blindly.

### Fix — `ai/translator.py`
`extract_function_name()`'s single-line lookahead is now a small
forward-merge: when a candidate line has unbalanced parens (a parameter
list still open), subsequent lines are merged in (capped at 20 lines, a
sanity bound) until the parens close, before checking for the opening `{`.
Brace-depth accounting from session 24 is preserved across the merged span.

### Verification
- The targeted case now resolves correctly:
  `__mingw_invalidParameterHandler` → `handle_invalid_parameter`.
- Re-checked the full existing regression set (the Bishop/leaked-context
  case, the other 6 clean functions from that run, and the 5 real
  standalone-destructor cases) — all unchanged.
- Went further this time: ran both the pre-session-24 and current
  `extract_function_name()` against **all 100** real completed
  translations in `semantic.db` (every binary, not just Chess.exe's
  7-function test run) and diffed every case where the result changed.

### A real, unresolved tension found during that broader sweep
9 of the 100 real functions changed result. Two are unambiguous fixes:
`Bishop` → `WinMainCRTStartup` (session 24's own target) and a case where
the old code grabbed `x` from a one-line accessor (`struct Piece { int
x() const {...} }`) sitting textually before the real function `Move` —
depth-tracking correctly prefers `Move`.

But two others are genuine **regressions**, and they reveal *why*
depth-based rejection isn't a clean fix in general: pass 4's own system
prompt explicitly instructs the model to "emit a skeleton class definition
... where evidence supports it," so the model legitimately nests its real,
substantive answer inside a class wrapper as designed behavior, not as a
leak. One case: the real function was a `string` constructor, and the
model's genuine, substantive answer was `class string { public: string(...)
{ ...real logic... } };` — nested at depth 1. Depth-tracking skips it and
instead matches a later, depth-0 helper function the model also wrote
(`static unsigned long long length(...)`), returning `length` — actively
*more* misleading for a string constructor than the old code's answer
(`string`). A second case (`Draw` → `Render`, a real, substantive nested
method calling `Chess::DisplayBoard`) shows the same pattern.

Checked whether a simpler combined heuristic (e.g. "also require a
non-empty body") resolves both directions — it doesn't: the wrongly-matched
`Piece::x()` accessor *does* have a real (if trivial) body, so an
emptiness filter alone lets it back through. The two directions
(prefer-depth-0 vs. prefer-nested-substantive) are in genuine conflict, and
no simple structural signal (depth, body-emptiness, or a combination)
cleanly resolves both — the signal that would actually work is
cross-referencing a candidate's signature against the *original Ghidra
signature* (parameter shape), which is a materially bigger change than a
name-extraction tweak.

### Decision: left as-is, not patched further
Rather than trade one set of real bugs for another with an unproven quick
heuristic, the depth-tracking fix from session 24 stays as shipped — on
balance across the sweep it fixes clear cases and mostly falls back safely
elsewhere, with two confirmed regressions. Properly disambiguating
"legitimate nested class reconstruction" from "leaked/injected boilerplate"
is treated as real, open work for the planned Phase 4 validation layer
(which will have access to more structured evidence to make this call),
not a same-day patch. Logged as known-edge-case #11.

---

## What was built — session 26

Architecture-mission Phase 3: evidence-based, whole-program-aware
prompting (mission items #1 and #6). Wires the knowledge graph populated
in Phases 1-2 into the actual prompts for the first time — purely
additive prompt *content*; the linear 1-6 pass loop, name-lock, and callee
guard are all untouched.

### `analyzer/types_db.py`
Two small lookups needed to resolve graph data for prompts: `get_entity(entity_id)`
(reverse lookup by id — only `get_entity_id(binary, kind, key)` existed
before) and `get_entities_by_kind(binary, kind)` (all entities of one kind
in a binary, e.g. every detected `type`).

### `ai/translator.py`
Three new formatting helpers, run once per function before the pass loop:
- `_format_deterministic_facts()` — renders Phase 2's `entity_facts`
  (calling convention, natural loops, propagated constants, alias hints)
  into prompt text.
- `_format_library_hints()` — walks this function's `references`
  relationships to STL `type` entities and renders "use the real type,
  don't reinvent its internals" hints.
- `_format_whole_program_context()` — lists every `type`-kind entity
  known anywhere in the binary, not just this function's direct
  callers/callees.

`translate()` now computes all three (via the function's own entity,
looked up by `binary + address`) right after fetching the existing
`recovered_types`/`callee_summaries`/`caller_summaries` context, and
threads them into every `build_user_prompt()` call in the pass loop.

### `ai/prompts.py`
`build_user_prompt()` gained three new optional sections: `DETERMINISTIC
EVIDENCE` (pass ≤4 — most useful through class reconstruction, less
relevant once only beautification remains), `KNOWN LIBRARY TYPES
REFERENCED` and `WHOLE-PROGRAM CONTEXT` (both pass ≥3, matching
`RECOVERED TYPES FROM OTHER FUNCTIONS`'s existing gating).

### Verification against real data
- Ran all three formatting helpers directly against the real `entity_facts`/
  `relationships` already sitting in `semantic.db` from the earlier
  Chess.exe run (87 real facts, real `references` relationships to
  `std::allocator`/`std::basic_string`) — all render correctly. (One early
  scare: a formatted string's em-dash printed as `�` in the console;
  confirmed via `hex(ord(ch))` that the actual stored character is the
  correct `U+2014` — purely a console rendering artifact, not real data
  corruption.)
- Assembled a full real pass-4 prompt end-to-end via `build_user_prompt()`
  with real evidence/hints/context strings — output is well-formed.
- Ran the actual `MultiPassTranslator.translate()` method (not a
  reimplementation) against a real, already-fully-completed Chess.exe
  function. It correctly resumed straight through all 6 passes (0 loop
  iterations), which meant the new entity-lookup and all three formatting
  calls executed for real, unconditionally, with zero exceptions, before
  ever reaching the (unrelated, pre-existing) final summary step — which
  failed only because no live Bonsai server is running right now, nothing
  to do with this session's changes.

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

4. **Call graph query joins on name, not address (partially fixed, sessions 16 and 21)**
   — the *cross-binary* version of this (two different binaries sharing a
   callee name, e.g. `atexit`) is fixed: `call_graph` is now binary-scoped.
   Session 21 fixed the *same-binary* version too, but only for the new
   knowledge-graph `relationships` table: Ghidra's export now also emits
   address-qualified `calleeRefs`, and `main.py` uses them to record `calls`
   relationships against the correct entity even when two functions share a
   name (verified against the real, previously-cited case: Chess.exe's two
   `__do_global_ctors`). **Still open:** the *existing* `call_graph` table
   and `get_callee_summaries`/`get_caller_summaries` (what actually feeds
   prompts today) are untouched and still join by name — this gap is only
   closed for the graph's own data so far, not for what the AI passes
   currently see. The callee-guard logic in `ai/translator.py` also still
   treats callees as bare name strings. Fully closing this for prompt
   context is a later phase of the architecture-mission plan (see session 21).

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

8. **Self-recursive "delegate to the real implementation" stubs** — confirmed
   in a real Chess.exe Bonsai run's `recovered.cpp`: for thin std-library-ish
   functions (`operator_new`, `string`'s constructors, `_M_local_data`,
   `_Allocator`, `_M_dispose`, `operator>>`, `_M_create`, …), the AI
   sometimes writes a body that calls *itself* under the exact same
   name/signature while commenting that it's "delegating to the underlying
   implementation" — e.g. `void _M_dispose(void) { _M_dispose(); return; }`.
   That's genuine infinite recursion if the code were ever compiled and run,
   not a stylistic rough edge — the model appears to hallucinate a distinct
   external symbol with the same spelling as the local one it just defined.
   Seen across several functions in the same file, so not a one-off. Not yet
   root-caused or fixed — unrelated to the Bonsai reasoning-leak issues above
   (this file also happened to contain a raw chain-of-thought dump from that
   separate bug, but this self-recursion pattern showed up in functions
   untouched by that leak too). Needs a closer look once a clean re-run
   (reasoning off) is available to compare against — worth checking whether
   it's Bonsai-specific or shows up with other providers too.

9. ~~`ir_builder.py`'s varnode regex likely never matches real p-code
   text~~ **Fixed in session 23.** `_VARNODE_RE` had no whitespace
   tolerance after the commas inside a `(space,offset,size)` token, but
   Ghidra's real `PcodeOpAST.toString()` output always includes a space
   there. Confirmed directly: **0 of 1939** real p-code ops from a real
   `find.exe` export matched the old regex; a whitespace-tolerant version
   matched 100%. Combined with a second bug (naive comma-splitting shredded
   multi-input operands), the practical effect — confirmed by directly
   comparing old vs. new parse output on a real function — was that nearly
   every op with an output varnode rendered as a garbled fragment of its
   own output token (e.g. `"(register,"`) with zero operands, not
   assembly-like text at all. Since `build_ir()` feeds every AI prompt for
   passes ≤3, this means the "P-CODE IR" context has been close to
   meaningless noise for this entire project's history. Both bugs are now
   fixed (same fix already applied to `analyzer/cfg_builder.py`'s copy of
   this logic in session 22); see session 23. Not retroactive — previously
   completed translations in `semantic.db` were produced against the old,
   garbled context and aren't automatically redone.

10. ~~`extract_function_name()`'s `has_brace` lookahead only checks one
    line ahead~~ **Fixed in session 25.** A function whose signature spans
    more than two lines (common after pass 6 beautification reformats a
    multi-parameter signature onto separate lines) never had its opening
    `{` detected, so a real in-body rename went undetected and
    `function_index.txt`/the recovered.cpp banner kept showing the
    original Ghidra name even though the code itself was renamed. Fixed by
    merging continuation lines while a parameter list is still open,
    before checking for `{`. See session 25.

11. **Depth-tracking (session 24's destructor-nesting fix) can miss or
    mis-pick a name when the model's real, substantive answer is
    legitimately nested inside a class wrapper (found in session 25, not
    fixed)** — pass 4's system prompt explicitly asks the model to wrap a
    reconstructed function in a class skeleton "where evidence supports
    it," so nesting isn't reliably a leak signal. Confirmed two real
    regressions from requiring depth 0: a `string` constructor's genuine,
    substantive nested answer (`class string { public: string(...) {
    ...real logic... } };`) gets skipped in favor of a later, unrelated,
    depth-0 helper function the model also wrote (`length`) — actively
    more misleading than the pre-session-24 result (`string`). A second
    case (`Draw`→`Render`, a real nested method with substantive logic)
    shows the same pattern. Tried an emptiness-filter (reject only
    stub/empty bodies) as a possible combined fix — doesn't work either,
    since a wrongly-matched one-line accessor (`int x() const { return
    *(int*)this; }`) has a real, non-empty body too. No simple structural
    signal (depth, emptiness, or a combination) reliably resolves both
    directions; the signal that would actually work is cross-referencing a
    candidate against the *original Ghidra signature* (parameter shape),
    which is materially bigger than a name-extraction tweak. Deliberately
    left unpatched rather than trading one set of bugs for another with an
    unproven quick heuristic — treated as real work for the planned Phase 4
    validation layer, which will have more structured evidence to make this
    call properly.
