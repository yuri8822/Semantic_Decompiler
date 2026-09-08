"""
Single-pass system prompt and user-prompt builder.

Idiomatic-rewrite pass, not regeneration: user-tested prompt phrasing
(session 2026-07-30) that spells out concrete decompiler-artifact-to-
idiomatic-C++ translations (operator_new -> new, pointer arithmetic ->
array indexing, etc.) rather than a long list of hard constraints.
"""

SYSTEM_PROMPT = """The following is Ghidra decompiled code. Translate common decompiler artifacts into conventional, idiomatic C/C++ code. Really think about how certain pieces of code can be written in a much simpler and more human-readable way instead of mechanically preserving the decompiler output.

Some examples of desired translations:

operator_new(...) or operator_new__(...) → new
operator_delete(...) → delete

Explicit constructor calls such as:

Foo *obj = (Foo *)operator_new(sizeof(Foo));
Foo::Foo(obj);

→ new Foo()

Pointer arithmetic such as:

*(T **)(base + index * 8)

→ base[index]

Nested pointer arithmetic should be rewritten as multidimensional array indexing where appropriate (e.g. board[row][col] instead of chained pointer dereferences).
Replace temporary decompiler variables (lVar1, pvVar2, iVar3, uVar4, local_XX, etc.) with meaningful names or eliminate them entirely when unnecessary.
Convert explicit pointer dereferences into normal member access (this->member) and array indexing whenever possible.
Replace raw byte-sized allocations with their semantic equivalents (e.g. operator_new(0x40) allocating eight pointers should become new T[8] when the type can be inferred).
Remove unnecessary casts introduced by the decompiler.
Simplify verbose control flow and expressions into conventional C/C++ constructs without changing behavior.
Infer types from context where it is safe to do so.
Preserve the original logic exactly, but prefer the code that a human would naturally write rather than what a decompiler emits.

The goal is to produce code that looks like original source code written by an experienced C/C++ programmer, not decompiler output. Rename variables where appropriate to improve readability, but do not invent behavior that is not present in the original code.

VERY IMPORTANT: ONLY OUTPUT C++ CODE AND NO OTHER PROSE IN YOUR ANSWER

Code:"""


def build_user_prompt(function_data: dict, ir: str = "", cfg_summary: str = "",
                       api_context: str = "", deterministic_evidence: str = "",
                       library_hints: str = "", whole_program_context: str = "",
                       recovered_types: str = "", callee_summaries: list = None,
                       caller_summaries: list = None, previous_attempt: str = "",
                       review_feedback: str = "") -> str:
    parts = []
    parts.append(f"FUNCTION: {function_data['name']}")
    parts.append(f"SIGNATURE: {function_data.get('signature', '')}")

    if function_data.get("strings"):
        parts.append("\nREFERENCED STRINGS:")
        for s in function_data["strings"][:20]:
            parts.append(f'  "{s}"')

    if function_data.get("imports"):
        parts.append("\nIMPORTED CALLS: " + ", ".join(function_data["imports"][:15]))

    if api_context:
        parts.append("\nKNOWN API SIGNATURES:")
        parts.append(api_context)

    if deterministic_evidence:
        parts.append("\nDETERMINISTIC EVIDENCE (static analysis, not AI-inferred):")
        parts.append(deterministic_evidence)

    if library_hints:
        parts.append("\nKNOWN LIBRARY TYPES REFERENCED:")
        parts.append(library_hints)

    if recovered_types:
        parts.append(
            "\nRECOVERED TYPES ALREADY ESTABLISHED FOR THIS BINARY "
            "(the only definitions you may use for member-access syntax "
            "on a raw offset — an exact name match, not inspiration for "
            "a new one):"
        )
        parts.append(recovered_types)

    if whole_program_context:
        parts.append("\nWHOLE-PROGRAM CONTEXT:")
        parts.append(whole_program_context)

    if callee_summaries:
        parts.append("\nCALLEE SUMMARIES:")
        for c in callee_summaries[:8]:
            parts.append(f"  {c['signature']} — {c['summary']}")

    if caller_summaries:
        parts.append("\nCALLER SUMMARIES:")
        for c in caller_summaries[:8]:
            parts.append(f"  {c['signature']} — {c['summary']}")

    if ir:
        parts.append("\nP-CODE IR:")
        parts.append(ir[:3000])

    if cfg_summary:
        parts.append("\nCFG SUMMARY:")
        parts.append(cfg_summary)

    parts.append("\nDECOMPILED CODE TO RECONSTRUCT:")
    parts.append(function_data.get("decompiled", ""))

    if previous_attempt and review_feedback:
        parts.append("\nPREVIOUS ATTEMPT (rejected by review):")
        parts.append(previous_attempt)
        parts.append("\nREVIEWER FEEDBACK — fix ONLY these specific issues, changing nothing else:")
        parts.append(review_feedback)

    parts.append("\nReturn the complete reconstructed function only.")
    return "\n".join(parts)


# Second agent in the review loop. Its main focus is different from a
# generic code reviewer: judge how THOROUGHLY the refactor agent converted
# low-level, Ghidra-decompiled C into high-level, idiomatic C/C++ -- not
# just "is this readable" but "is there decompiler residue left behind that
# SYSTEM_PROMPT asked to have translated away." Garbage-output detection and
# a behavior-preservation guard still apply, but as secondary checks, not
# the main event.
REVIEWER_SYSTEM_PROMPT = """You are reviewing how thoroughly a low-level, Ghidra-decompiled C \
function was converted into high-level, idiomatic C/C++, against the ORIGINAL DECOMPILED CODE \
below (ground truth for what the program actually does).

FIRST: confirm the ANNOTATED CODE is ONLY the one function being reviewed — \
nothing else. FAIL immediately, without needing any other reason, if it contains \
any of the following: prose or an explanation outside a code comment; an echo of \
this prompt's own labels (e.g. "FUNCTION:", "SIGNATURE:", "P-CODE IR:") or any \
other input verbatim; a class/struct/enum/typedef definition; more than one \
top-level function; or anything else that isn't valid C/C++ belonging to this \
one function.

Otherwise, your MAIN JOB is judging the conversion itself. Check the ANNOTATED \
CODE for leftover low-level decompiler residue that should have been translated \
away, specifically:
- operator_new(...)/operator_new__(...)/operator_delete(...) left as raw calls
  instead of new/delete.
- An explicit allocate-then-construct pattern left as two statements
  (`Foo *obj = (Foo *)operator_new(sizeof(Foo)); Foo::Foo(obj);`) instead of
  collapsed into `new Foo()`.
- Raw pointer arithmetic (`*(T **)(base + index * 8)`) left instead of array
  indexing (`base[index]`), including nested cases that should be
  multidimensional indexing (`board[row][col]`) instead of chained
  dereferences.
- Decompiler temp variable names (lVar1, pvVar2, iVar3, uVar4, local_XX, etc.)
  left unrenamed when a meaningful name was clearly inferable.
- Raw offset dereferences left instead of member access (`this->field`) where
  a real field/type was already evidenced.
- Byte-sized raw allocations left as-is instead of their semantic equivalent
  (e.g. `operator_new(0x40)` for eight pointers should be `new T[8]` once the
  type is inferable).
- Unnecessary decompiler-introduced casts left in place.
- Verbose, mechanical control flow/expressions left unsimplified when a
  conventional C/C++ equivalent was clearly available.

As secondary checks (real defects, not the main focus, but still grounds for
FAIL):
- Any actual change in behavior versus the original — a different condition,
  a call that wasn't there, a different return value, anything that changes
  what the program actually does. Restructuring control flow, merging
  statements, or reshaping expressions is fine as long as the behavior is
  identical.
- Syntax that genuinely wouldn't compile as conventional C/C++.

Do NOT fail the code for style/naming/formatting preferences that aren't one
of the specific issues above.

Respond in exactly this format:
Line 1: "VERDICT: PASS" or "VERDICT: FAIL" (nothing else on that line).
If FAIL, follow with a numbered list of the SPECIFIC issues found, each
concrete enough for another pass to act on — quote the offending code, and for
each leftover artifact say what it should have become (e.g. "operator_new(0x18)
on line 4 should be `new Piece()`"). Do not restate the rules above, and do not
soften an issue into a suggestion."""


def build_review_prompt(function_data: dict, refactored_code: str,
                         api_context: str = "", deterministic_evidence: str = "",
                         library_hints: str = "", recovered_types: str = "",
                         callee_summaries: list = None, caller_summaries: list = None) -> str:
    parts = []
    parts.append(f"FUNCTION: {function_data['name']}")
    parts.append(f"SIGNATURE: {function_data.get('signature', '')}")

    if api_context:
        parts.append("\nKNOWN API SIGNATURES:")
        parts.append(api_context)

    if deterministic_evidence:
        parts.append("\nDETERMINISTIC EVIDENCE (static analysis, not AI-inferred):")
        parts.append(deterministic_evidence)

    if library_hints:
        parts.append("\nKNOWN LIBRARY TYPES REFERENCED:")
        parts.append(library_hints)

    if recovered_types:
        parts.append("\nRECOVERED TYPES ALREADY ESTABLISHED FOR THIS BINARY:")
        parts.append(recovered_types)

    if callee_summaries:
        parts.append("\nCALLEE SUMMARIES:")
        for c in callee_summaries[:8]:
            parts.append(f"  {c['signature']} — {c['summary']}")

    if caller_summaries:
        parts.append("\nCALLER SUMMARIES:")
        for c in caller_summaries[:8]:
            parts.append(f"  {c['signature']} — {c['summary']}")

    parts.append("\nORIGINAL DECOMPILED CODE (ground truth for logic/structure):")
    parts.append(function_data.get("decompiled", ""))

    parts.append("\nANNOTATED CODE TO REVIEW:")
    parts.append(refactored_code)

    parts.append("\nReturn your verdict now.")
    return "\n".join(parts)
