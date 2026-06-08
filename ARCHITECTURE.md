# AI-Assisted Semantic Decompiler Architecture

## Vision

This project is NOT a traditional decompiler.

Traditional decompilers attempt:
binary → low-level pseudocode

This project attempts:
binary → semantically reconstructed high-level C++

using:

* static analysis
* Ghidra
* AI reasoning
* semantic memory
* iterative refinement

---

# Core Philosophy

Do NOT replace Ghidra.

Leverage Ghidra for:

* disassembly
* CFG recovery
* SSA
* p-code
* variable recovery
* decompilation

Use AI for:

* semantic understanding
* naming
* abstraction reconstruction
* class inference
* improving readability
* recovering intent

---

# Primary Pipeline

```text
EXE
 ↓
Ghidra Analysis
 ↓
P-Code + Decompiled C
 ↓
IR Normalization
 ↓
AI Semantic Reconstruction
 ↓
Global Type Recovery
 ↓
Multi-Pass Refinement
 ↓
Recovered C++ Project
```

---

# Major Components

## 1. Binary Analysis Layer

Handled by Ghidra.

Responsibilities:

* parse executable
* recover functions
* generate CFGs
* generate p-code
* recover stack variables
* identify imports
* recover strings

---

# 2. IR Layer

Purpose:
normalize Ghidra output into AI-friendly representation.

Potential IR improvements:

* SSA conversion
* explicit variable lifetimes
* typed operations
* memory access abstraction
* CFG serialization

---

# 3. Semantic Memory Layer

Persistent database storing:

* inferred structs
* inferred classes
* function signatures
* naming mappings
* API mappings
* inferred enums
* RTTI discoveries

Suggested storage:
SQLite initially.

---

# 4. AI Reconstruction Layer

Inputs:

* decompiled C
* p-code
* CFG summaries
* imports
* strings
* neighboring function summaries
* recovered types

Outputs:

* cleaner pseudocode
* improved C++
* inferred abstractions

---

# 5. Multi-Pass Pipeline

Recommended passes:

Pass 1:
Basic cleanup.

Pass 2:
Variable renaming.

Pass 3:
Type inference.

Pass 4:
Class reconstruction.

Pass 5:
Cross-function consistency.

Pass 6:
Beautification.

---

# Critical Design Principles

## Never rely solely on AI

Static analysis must remain authoritative.

AI should enhance semantics, not invent logic.

---

# Use Retrieval-Augmented Context

Large binaries exceed context windows.

Use:

* function-level context
* neighboring call graph nodes
* semantic summaries
* retrieved type info

Avoid feeding entire binaries.

---

# Prefer Function-Level Translation

Do NOT chunk arbitrary instruction ranges.

Preferred units:

* functions
* logical CFG regions
* strongly connected CFG components

---

# Important Future Enhancements

## CFG Serialization

Represent control flow explicitly:

```json
{
  "blocks": [...],
  "edges": [...]
}
```

This helps AI understand loops and branches.

---

# Symbolic Execution

Potential future integration:

* angr
* Triton
* miasm

Useful for:

* constant propagation
* path reasoning
* deobfuscation

---

# RTTI & Class Recovery

Important for C++ binaries.

Recover:

* vtables
* constructors
* destructors
* inheritance relationships

---

# Validation Systems

Potential future systems:

* recompile-and-compare
* CFG similarity scoring
* instruction similarity scoring

---

# Recommended Initial MVP

Input:
single function.

Process:
Ghidra decompilation → AI cleanup.

Output:
cleaner pseudocode.

Do NOT attempt full-project reconstruction initially.

---

# Recommended Research Topics

* SSA form
* CFG analysis
* compiler optimizations
* binary lifting
* symbolic execution
* decompilers
* LLVM IR
* type propagation
* abstract interpretation

---

# Existing Tools Worth Studying

Decompiler Tools:

* Ghidra
* RetDec
* Binary Ninja
* IDA Pro

Binary Analysis:

* angr
* Triton
* miasm
* Capstone
* Keystone

Compiler Infrastructure:

* LLVM

---

# Long-Term Vision

Eventually achieve:

binary
→ semantic IR
→ AI reconstruction
→ consistent recovered project
→ human-readable near-source-level code

This is a research-grade problem involving:

* reverse engineering
* program analysis
* compilers
* AI reasoning
* semantic reconstruction