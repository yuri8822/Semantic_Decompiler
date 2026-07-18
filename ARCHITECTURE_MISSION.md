# Semantic Decompiler — Architecture Mission

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
