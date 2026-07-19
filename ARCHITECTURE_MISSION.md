# Semantic Decompiler — Architecture Mission

## Progress

Scoped down and being executed as an 8-phase plan (excludes items #8/#9/#17
and the recompile-equivalence part of #18 below — see rationale in the plan
file). Tracked at `C:\Users\umar\.claude\plans\dynamic-dreaming-alpaca.md`;
narrated per-phase in `BUILD_PROGRESS.md`.

- **Phase 1 — done (BUILD_PROGRESS.md, session 21).** Knowledge-graph schema
  (`entities`/`entity_facts`/`relationships`/`contradictions`) added to
  `analyzer/types_db.py`, purely additive alongside the existing tables.
  Ghidra export now also emits address-qualified callees (`calleeRefs`), so
  `relationships` can resolve same-named functions within one binary
  correctly. Verified against a real `analyzeHeadless` run, not just a
  synthetic DB.
- **Phase 2 — done (BUILD_PROGRESS.md, session 22).** Deterministic evidence
  generation: real dominator-based natural-loop detection, bounded
  intraprocedural constant propagation, calling-convention extraction, and
  partial alias hints (`analyzer/cfg_builder.py`), plus a best-effort
  STL/library signature detector (`analyzer/library_signatures.py`) tagging
  `known_apis.library`. Verified against the same real `find.exe` export —
  along the way, found and fixed two real parsing bugs in this new code
  (a varnode regex missing whitespace tolerance, and a naive comma-split
  that shreds multi-input operands).
- **`ir_builder.py` bug fix — done (BUILD_PROGRESS.md, session 23, out of
  band from the phase plan).** The same two bugs found in session 22 also
  existed in `ir_builder.py` itself; user confirmed the app isn't in
  production and asked for it to be fixed immediately rather than wait.
  Fixed and verified against real data — old vs. new parse output compared
  side-by-side on a real function confirmed the practical severity was
  worse than the raw match-rate statistic implied: nearly every op with an
  output varnode had been rendering as a garbled fragment of its own
  output token with zero operands, not degraded-but-readable assembly.
  Not retroactive — existing `semantic.db` translations were produced
  against the old, garbled p-code context.
- **`extract_function_name()` destructor-nesting bug fix — done
  (BUILD_PROGRESS.md, session 24, out of band from the phase plan).**
  Found while spot-checking a fresh `--restart` run's real output: a
  function got mislabeled `Bishop` because pass 4 leaked an unrelated
  "recovered types" block (containing `virtual ~Bishop() {}`) into its
  output, and the name-extraction regex had no nesting-awareness, so it
  grabbed the destructor's class name instead of the real, still-intact
  top-level function. Fixed with brace-depth tracking (only accept a
  candidate at depth 0); verified against the real leaked case, the 6
  clean functions from the same run, and real legitimate standalone
  destructor translations already in the DB (no regression). Also found —
  but deliberately left unfixed at the time, out of scope for what was
  asked — a separate, pre-existing, unrelated limitation: multi-line
  signatures (2+ lines before the opening brace) aren't detected at all,
  so some real in-body renames never reach `function_index.txt`/the banner.
- **Multi-line-signature fix — done (BUILD_PROGRESS.md, session 25).**
  Fixed the limitation just above. While verifying it, ran a much broader
  sweep (all 100 real completed translations, not just the 7-function test
  set) and found a genuine, unresolved tension in session 24's own
  depth-tracking fix: pass 4 legitimately nests a real, substantive answer
  inside a class skeleton by design (its own system prompt asks for this),
  so "nested = leaked" isn't reliably true. Two real regressions confirmed
  (a string constructor's genuine nested answer lost in favor of an
  unrelated depth-0 helper function; a `Draw`→`Render` nested method lost
  the same way). No simple structural heuristic (depth, body-emptiness, or
  a combination) resolves both directions — the real fix needs to
  cross-reference a candidate against the original Ghidra signature, which
  is materially bigger than a name-extraction tweak. Deliberately left
  unpatched rather than trade one set of bugs for another; logged as
  known-edge-case #11, explicitly deferred to Phase 4's validation layer.
- **Phase 3 — done (BUILD_PROGRESS.md, session 26).** Evidence-based,
  whole-program-aware prompting: `ai/prompts.py` gained three new sections
  (deterministic evidence, known-library hints, whole-program type
  context) fed from the Phase 1/2 knowledge graph; `ai/translator.py`
  fetches and threads them through every pass. Purely additive prompt
  content — the linear pass loop, name-lock, and callee guard are
  unchanged. Verified against real `entity_facts`/`relationships` already
  in `semantic.db`, and by running the actual `translate()` method against
  a real completed function (resumed cleanly through all 6 passes,
  exercising the new code with zero exceptions).
- **Phases 4-8 — not started.** A validation layer, confidence-gated
  overwrite + contradiction detection, the iterative refinement loop,
  semantic checkpoints/quality metrics, and `output/writer.py` catching up
  to the graph. Sequenced risk-ascending; each phase requires a separate
  go-ahead before starting.

---

## Mission Statement

Evolve the Semantic Decompiler from a function-by-function AI translation
pipeline into a whole-program semantic reconstruction system: one that
builds a knowledge graph of the binary, recovers structure deterministically
wherever possible, and uses AI for semantic interpretation rather than as
the entire reconstruction engine.

---

## 1. Shift From Function-Centric to Whole-Program Reconstruction

Current pipeline:

```
Function
 ↓
Prompt
 ↓
LLM
 ↓
Better Function
```

Functions do not exist independently. They rely on:

- Classes
- Globals
- Types
- Calling conventions
- Memory layout
- Inheritance
- Neighboring functions

Instead:

```
Binary
 ↓
Whole-program semantic graph
 ↓
Recover globals
 ↓
Recover classes
 ↓
Recover types
 ↓
Recover function semantics
 ↓
Beautification
```

Every function should be reconstructed using knowledge gathered from the
entire binary.

---

## 2. Turn the Semantic Database into a Knowledge Graph

Instead of storing only names, types, and summaries, store **entities**:

- Kind
- Confidence
- Evidence
- Relationships

Example entity:

```
Player
Confidence: 92%
Constructor found
Vtable detected
Field offsets recovered
References Inventory
Calls Update()
```

The semantic database becomes reasoning infrastructure rather than just a
cache.

---

## 3. Confidence Scores Everywhere

Every recovered fact should include a confidence score.

```
Player
Confidence: 91%

Evidence:
- RTTI
- Constructor pattern
- Four references
- Vtable
```

Later passes should only overwrite information if confidence increases.

---

## 4. Replace Linear Passes With Iterative Refinement

Current: Cleanup → Rename → Types → Classes → Consistency → Beautify.

Problem: names influence types, types influence classes, classes influence
names — a strictly linear pass order can't resolve these dependencies.

Instead, iterate:

```
Names → Types → Classes → repeat until convergence
```

Like compiler optimization passes.

---

## 5. AI Should Do Less

Whenever deterministic analysis can solve something, prefer deterministic
analysis:

- Calling conventions
- Stack variables
- CFG
- SSA
- Dominators
- Constant propagation
- Loop detection
- Variable lifetime
- Pointer aliasing (partial)

Reserve AI for semantic interpretation.

---

## 6. Prompt Using Evidence

Instead of asking the model to recover code from scratch, provide
structured evidence:

- Stack frame
- Known APIs
- Strings
- CFG
- SSA
- Types
- Globals
- Constants
- Structures
- Field offsets
- Inheritance
- Caller/callee information

The model reasons from evidence rather than free-form text.

---

## 7. Build a Validation Layer

```
LLM
 ↓
Validator
 ↓
Accept / Reject
```

Validator checks:

- Call count changed?
- Return removed?
- Global invented?
- Unknown API invented?
- Branches changed?
- Missing switch cases?
- Accidental recursion?
- Stack variables disappear?
- CFG changed?

Rejected outputs should be regenerated.

---

## 8. Compare ASTs Instead of Text

Compare syntax trees instead of strings. Formatting differences become
irrelevant while semantic changes remain detectable.

---

## 9. Introduce a Semantic Intermediate Representation

Current:

```
P-code → LLM → C++
```

Suggested:

```
P-code → Semantic IR → LLM → C++
```

Semantic operations such as:

- `LOAD_FIELD`
- `CALL_METHOD`
- `ITERATE_VECTOR`
- `DESTROY`
- `COPY`
- `COMPARE_STRING`

This dramatically simplifies prompts.

---

## 10. Let Memory Grow Richer

Instead of summaries only, store:

- Constructors
- Destructors
- Vtables
- Inheritance
- Field offsets
- Aliases
- Confidence
- Example usages

---

## 11. Detect Semantic Contradictions

If an entity changes meaning:

```
Player → Renderer
```

Do not silently overwrite. Raise a contradiction requiring resolution.

---

## 12. Version Knowledge

Do not overwrite previous conclusions. Store:

- History
- Confidence
- Timestamp
- Reason
- Previous names

Allow semantic evolution over time.

---

## 13. Think in Entities Instead of Functions

Everything revolves around entities:

```
Inventory
 ↓
Fields
 ↓
Methods
 ↓
Relationships
 ↓
Ownership
 ↓
Strings
 ↓
Constants
 ↓
RTTI
 ↓
Confidence
```

Functions become one view into the graph.

---

## 14. Semantic Checkpoints

Checkpoint each major pass. Measure quality. If quality decreases, roll
back automatically.

---

## 15. Measure Reconstruction Quality

Track metrics:

- Classes recovered
- Types recovered
- Variable names recovered
- Unknown locals
- Unknown globals
- Unknown calls
- Recovered enums
- Vtables
- Inheritance
- Average confidence
- Prompt tokens
- Output tokens
- Runtime
- Rollback rate
- Validation failures

---

## 16. Recover Libraries First

Detect libraries such as:

- `std::vector`
- `std::string`
- Qt
- SDL
- Boost
- DirectX
- GLM
- RapidJSON

Recovering known libraries simplifies the entire binary.

---

## 17. Move Symbolic Execution Earlier

```
Binary → SSA → Symbolic execution → Constant propagation → Alias reduction → LLM
```

This reduces hallucinations.

---

## 18. Long-Term Vision

```
Binary
 ↓
Static Analysis
 ↓
Dynamic Traces (optional)
 ↓
Knowledge Graph
 ↓
Constraint Solver
 ↓
LLM
 ↓
Validation
 ↓
Compilation
 ↓
Binary Equivalence Testing
 ↓
Recovered Project
```

AI becomes one component rather than the entire engine.

## Recommended Future Architecture

```
1. Ghidra Export
        ↓
2. Static Analysis Engine
        ↓
3. Whole-Program Semantic Graph
        ↓
4. Deterministic Recovery
   • CFG
   • SSA
   • Constants
   • APIs
   • Types
   • Globals
   • RTTI
   • VTables
        ↓
5. Constraint Solver
        ↓
6. AI Semantic Reconstruction
        ↓
7. Validation Engine
        ↓
8. Iterative Refinement
        ↓
9. Recompilation & Structural Verification
        ↓
10. Final C++ Project + Explainable Knowledge Graph
```
