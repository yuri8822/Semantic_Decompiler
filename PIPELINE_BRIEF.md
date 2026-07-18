# Semantic Decompiler — Pipeline Brief (for review)

## Vision

Binary → semantically reconstructed high-level C++ (not just pseudocode).
Ghidra's static analysis is authoritative; AI adds naming, type inference,
class reconstruction, and intent — but must never invent logic that isn't
grounded in the actual decompiled/p-code input.

## Data flow

```
EXE
 → Ghidra headless analysis (ghidra_scripts/ExportAnalysis.java)
 → JSON export (CFG, strings, p-code SSA, callers/callees)
 → parse into pydantic models (analyzer/parse_output.py)
 → per function:
      p-code → readable IR              (analyzer/ir_builder.py)
      CFG summary                       (analyzer/cfg_builder.py)
      known-API context                 (analyzer/known_apis.py + types_db)
 → 6-pass AI translation, one function at a time (ai/translator.py)
      pass 1 cleanup
      pass 2 renaming
      pass 3 type inference
      pass 4 class reconstruction
      pass 5 cross-function consistency
      pass 6 beautification
 → SQLite semantic memory persists names/types/summaries
   across functions and across runs (analyzer/types_db.py, semantic.db)
 → output/writer.py assembles recovered.h / recovered.cpp / function_index.txt
```

Both entry points — `main.py` (CLI, rich progress bars) and `tui/` (Textual
UI) — call the same `pipeline.run_pipeline()` in `pipeline.py`. That module
is UI-agnostic: it communicates purely via `on_event()` callbacks
(`StageEvent` / `LogLine` / `ProgressEvent` / `DoneEvent` / `ErrorEvent`)
instead of printing directly, so it's the real orchestration core and the
best place to focus review.

## Key files and responsibilities

| File | Role |
|---|---|
| `pipeline.py` | Orchestrator: Ghidra step → load → DB seed → per-function translate loop → write. Handles resume/cache-skip, cancellation (`should_cancel()` threaded down to Ghidra-output-line / per-pass / per-function granularity), and throttled incremental output writes. |
| `main.py` | Thin CLI wrapper: argparse + rich console/progress bar rendering driven by `pipeline`'s event stream. No pipeline logic of its own anymore. |
| `tui/` | Textual TUI (`app.py` + widgets) — alternate presentation layer over the same `run_pipeline()`. Has Results, Memory (semantic.db browser), and Settings tabs. |
| `analyzer/ghidra_runner.py` | Shells out to `analyzeHeadless.bat` via `Popen`, streams stdout line-by-line (so `--verbose` / TUI log actually shows live progress), cancellable mid-run via `GhidraCancelled`. |
| `ghidra_scripts/ExportAnalysis.java` | Ghidra-side export: functions, p-code, CFG, callers/callees, strings. Catches `Throwable` (not just `Exception`) per-function so a single OOM doesn't abort the whole binary's analysis. |
| `analyzer/parse_output.py` | Pydantic v2 models for the full JSON export schema; skips malformed function entries with a warning instead of crashing. |
| `analyzer/ir_builder.py` | Parses p-code mnemonics (LOAD/STORE/CALL/BRANCH/arith/etc.) into human-readable IR for the AI prompt; unhandled ops fall back to a generic `dst = MNEMONIC(args)` rendering (not silently dropped). |
| `analyzer/cfg_builder.py` | Builds a networkx `DiGraph`: loop count, branch count, cyclomatic complexity — summarized into the prompt for passes 1-4. |
| `analyzer/types_db.py` | SQLite semantic memory. `functions` and `call_graph` tables are keyed by `(binary, address)` / `(binary, caller_addr, callee_name)` — **not** just a global address, since two different binaries can share a load address (fixed a real cross-binary data-corruption bug, see history below). Handles schema migration (cheap `ALTER TABLE` where possible, full rebuild via `_rebuild_functions_table`/`_migrate_call_graph` when a UNIQUE constraint itself needs to change) so new columns don't break existing local databases. |
| `analyzer/known_apis.py` | Curated Windows/CRT API signature database, seeded into the DB for prompt context. |
| `ai/prompts.py` | 6 pass-specific system prompts + the context assembler that builds each function's user prompt (decompiled code, IR, CFG summary, callee/caller summaries, known-API context, capped at ~3000 chars of p-code). |
| `ai/translator.py` | `MultiPassTranslator` — drives all 6 passes per function. Key mechanisms: **name-lock** (pass 2 is the only pass allowed to rename; its chosen name is force-injected into every later prompt as a "LOCKED FUNCTION NAME" instruction so passes 3-6 can't silently drift back to the raw Ghidra name); **callee guard** (after each pass ≥3, diffs which known callees actually disappear as *real call sites* — not just bare name presence — between the previous and current pass output, and reverts to the previous pass's code if any vanished); **pass-level resume** (scans stored `pass1_output`…`passN_output` for the longest contiguous prefix already done under the *same* provider and resumes right after it; a provider switch clears all stored pass data first via `clear_pass_data()` before any new work starts, closing a stale-mislabel window found in review). |
| `ai/llm_client.py` | Thin facade — resolves a provider from `ai/providers/` and forwards `complete()`. |
| `ai/providers/base.py` | `BaseProvider` ABC — one abstract method: `complete(system, user, pass_num) -> str`. |
| `ai/providers/{anthropic,xiaomi,ollama,bonsai}/*_provider.py` | Per-provider backends. Anthropic splits heavy (`claude-opus-4-8`, passes 3-4) vs fast (`claude-sonnet-4-6`, rest) models. Bonsai talks to a local llama.cpp server (OpenAI-compatible endpoint); currently ships with model "thinking" suppressed entirely (`enable_thinking: False`) after reasoning-on experiments caused several distinct failure modes (see history); also sets `repeat_penalty`/`repeat_last_n` to stop repetition-loop pollution. |
| `output/writer.py` | `ProjectWriter` — extracts struct/class/enum/typedef definitions via real brace-depth matching (not a single-level regex, which used to mis-nest classes with inline constructor bodies), dedupes them into `recovered.h`; writes `recovered.cpp` with address banners; writes `function_index.txt`. |
| `config.py` | Central config: `GHIDRA_PATH` (machine-specific, currently hardcoded), model names, pass count, output-write throttling constants. |

## Design invariants worth checking against

1. **Static analysis is authoritative** — AI enhances semantics, never invents logic. Any code path that lets an AI pass silently replace/fabricate a function body without a guard is a violation of this (see history: pass 3 once deleted a function body entirely and pass 4 hallucinated a replacement from scratch — this is what the callee guard exists to catch, but only partially).
2. **Per-binary scoping** — every DB read/write for functions and call-graph edges must include `binary`, not just `address`/`name`. Any new query or table added to `types_db.py` that keys only on address/name reintroduces the class of bug fixed in sessions 15-16.
3. **Provider-result isolation** — a function is only "done" for provider P if `final_cpp` is non-empty *and* the stored `provider` column equals P. Switching providers must clear stale pass data before writing anything new.
4. **Crash/interrupt safety** — output is written incrementally (throttled) and pass-level resume must not silently reuse another provider's or another binary's data.

## Known open gaps (unresolved, worth targeting in review)

- **Call-graph name collisions within a single binary**: `call_graph`/`get_callee_summaries`/`get_caller_summaries` still join by callee *name*, and Ghidra only exports names, not addresses. Two same-named functions in the *same* binary (confirmed real: `Chess.exe` has two `__do_global_ctors` at different addresses) can't be told apart. Fixing this needs callee addresses threaded through `ExportAnalysis.java` → `parse_output.py` → the callee-guard logic in `translator.py`.
- **Callee guard blind spot**: the pass ≥3 callee-drop check only protects a callee while its *original* Ghidra name is still what's being called. Once pass 2 renames a call site, that callee is unprotected for the rest of the pipeline.
- **No RTTI/vtable recovery pass** — pass 4's class reconstruction is pattern-based, not RTTI-driven. High value, not started.
- **No symbolic execution** (angr/Triton) integration for constant propagation / path reasoning.
- **No recompile-and-compare validation** — nothing verifies recovered C++ actually compiles or behaves equivalently.
- **p-code parser coverage gap** — `MULTIEQUAL` (SSA φ-node), `INDIRECT`, `SEGMENTOP`, `CPOOLREF`, `NEW` fall to a generic rendering rather than a hand-tuned one (not silently dropped, just less readable).
- **Type-harvesting regex** (now brace-depth-aware) still only extracted at a single conceptual definition per match; nested C++ templates aren't specifically handled.
- **Context window budget** — p-code capped at ~3000 chars per prompt; large functions get truncated with no frequency-based summarization fallback.
- **Self-recursive "delegate to real implementation" hallucination** — seen in real Bonsai output on thin std-library-ish functions (`operator_new`, `_M_dispose`, etc.): the model sometimes emits a body that calls itself under the identical name/signature while claiming to "delegate," which is genuine infinite recursion if compiled. Not yet root-caused; unclear if Bonsai-specific.
- **`GHIDRA_PATH` in `config.py`** is a hardcoded local machine path — not portable as-is.

## Suggested review focus areas

1. `pipeline.py` + `ai/translator.py` — the core orchestration and the name-lock/callee-guard/resume interplay; this is where most historical bugs lived (silent data corruption, stale cache hits, cancellation edge cases).
2. `analyzer/types_db.py` — schema/migration correctness, and whether *every* new query added since session 16 correctly scopes by `binary`.
3. `output/writer.py` — the brace-depth type extraction, for any remaining edge cases (e.g. templates, macros, string literals containing braces).
4. Provider implementations (`ai/providers/*`) — particularly Bonsai's response-cleaning logic, since it's had the most churn and known failure modes.
5. Cancellation plumbing (`should_cancel`) across Ghidra subprocess, per-pass translation, and the TUI — whether all three levels actually stop promptly and leave consistent on-disk/DB state.
