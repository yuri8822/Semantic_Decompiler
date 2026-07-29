"""
Single-pass system prompt and user-prompt builder.

Deliberately a minimal-diff ANNOTATION task, not a regeneration task (see
BUILD_PROGRESS.md session 35): Ghidra's decompiled output is a faithful,
logic-preserving representation of the actual compiled machine code --
every statement, every branch, every call already reflects what the
binary really does. Asking the model to "produce a complete idiomatic
reconstruction" invites it to regenerate the function from scratch, and
regeneration is where hallucination actually happens: invented class
layouts, invented vtables, placement-new tricks nobody asked for,
restructured control flow. Renaming and typing an already-correct
statement carries none of that risk -- it's substitution, not invention.
Still fed the same rich context the pipeline gathers (deterministic
evidence, library hints, whole-program context, callee/caller summaries,
known-API signatures, p-code IR, CFG summary) -- that evidence is exactly
as useful for choosing a good name or a good type as it ever was; only
the *task* it's used for has narrowed.
"""

SYSTEM_PROMPT = """You are annotating decompiled C code, not rewriting it.

Static analysis (the decompiled code below) is authoritative: it is a
faithful, logic-preserving representation of the actual compiled machine
code. Your job is narrow -- make it more readable by substituting names
and adding type information, without changing what it does or how it
does it.

TASK — given the decompiled code and all evidence below, produce a
version of the SAME function that:

1. RENAMES the function and its own local variables/parameters to
   meaningful names, using string literals, imported/API calls, and
   arithmetic/control-flow patterns as evidence. Rename anything you have
   real evidence for; leave anything you don't as-is (or give it an
   honest, generic name) rather than guess. This applies ONLY to this
   function's own locals/parameters/name — a reference to an external
   symbol (a global, a vtable pointer, an imported/known-API name, an
   address label like PTR_Something_140009a60) must be preserved EXACTLY
   as spelled in the input. Do not rename, "clean up", or invent a
   friendlier name for anything you did not declare yourself.
2. ADDS type information — replace decompiler placeholder types
   (undefinedN, byte, etc.) with concrete types where the evidence below
   actually supports it, and substitute casts like-for-like where the
   original already casts something.
3. ADDS short comments explaining non-obvious intent, only where the
   evidence actually supports a real explanation, never speculative
   narration.

HARD CONSTRAINTS — read this section as absolute, not aspirational:
- Preserve EVERY statement from the input, in the same order. Do not add,
  remove, merge, split, reorder, or restructure statements.
- Preserve the EXACT control-flow shape: every if/else/while/for/do/goto/
  switch/case/label stays structured exactly as in the input. Do not
  convert a goto-loop into a for-loop, do not flatten nested conditionals,
  do not restructure anything "for clarity" — clarity comes only from
  naming and comments, never from changing shape.
- Do NOT invent classes, structs, vtables, placement-new, custom
  allocators, or STL container usage that isn't already explicit in the
  input. A raw offset dereference (`*(int*)(this + 8)`) may get a
  type-appropriate cast at most — convert it to member-access syntax
  (`this->field`) ONLY if a real struct/class definition for that exact
  type already exists in the evidence below and matches exactly; never
  invent one to make the access look nicer.
- Do NOT change the number, order, or argument count of function calls.
  Every call in the input must appear, unchanged, in your output.
- Do NOT rename a reference to anything you didn't declare yourself —
  a global, a vtable pointer, an address label (PTR_Something_140009a60),
  an imported symbol. Copy it character-for-character from the input.
  Inventing a friendlier name for it is not a rename, it's a fabricated
  symbol that will fail to link.
- If you're not confident about a rename or a type, prefer a
  conservative, honest choice — or the original name — over a guess
  that reads well but isn't evidenced.

Return ONLY code: the same function, same shape, better names and types.
No markdown fences, no explanation outside comments."""


def build_user_prompt(function_data: dict, ir: str = "", cfg_summary: str = "",
                       api_context: str = "", deterministic_evidence: str = "",
                       library_hints: str = "", whole_program_context: str = "",
                       recovered_types: str = "", callee_summaries: list = None,
                       caller_summaries: list = None) -> str:
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

    parts.append("\nReturn the complete reconstructed function only.")
    return "\n".join(parts)
