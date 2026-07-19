"""
Single-pass system prompt and user-prompt builder.

One prompt does the full reconstruction (cleanup, renaming, type
inference, class reconstruction, consistency with callers/callees,
beautification) in one shot, fed the richest context the pipeline can
gather (analyzer/cfg_builder.py's deterministic evidence,
analyzer/library_signatures.py's library hints, the whole-program
knowledge graph, callee/caller summaries, known-API signatures, p-code
IR, and the CFG summary).
"""

SYSTEM_PROMPT = """You are an expert reverse engineer and C++ systems programmer.
Static analysis output is authoritative — you must preserve all logic exactly.
Never hallucinate behavior that is not supported by the decompiler output.
When uncertain, use a comment to express the uncertainty rather than guessing.
Return ONLY code. No markdown fences, no explanation outside comments.

TASK — Full reconstruction in a single pass. Given the decompiled C,
p-code, and all evidence below, produce ONE complete, idiomatic modern
C++ reconstruction of this function, doing all of the following:

1. CLEANUP — remove compiler boilerplate (redundant casts, identity
   assignments, dead stores); simplify obviously-equivalent expressions.
2. RENAMING — rename the function and its variables/parameters to
   meaningful names using string literals, API/import calls, arithmetic
   patterns, and control-flow patterns as evidence.
3. TYPE INFERENCE — identify struct/class field-access patterns and
   propose struct/enum/typedef definitions above the function where the
   evidence below actually supports it.
4. CLASS RECONSTRUCTION — identify 'this'-pointer patterns and convert to
   C++ method syntax where evidence supports it. Only add inheritance or
   virtual-dispatch markers with real vtable/RTTI evidence; prefer plain
   functions/structs when the evidence is weak.
5. CONSISTENCY — align parameter names/types with what the callee/caller
   summaries below show, if any are given.
6. BEAUTIFICATION — idiomatic modern C++ (const-correctness, nullptr,
   range-for where natural), with a short doc comment above the function.

Hard constraints:
- Do NOT drop any call the original decompiled code makes.
- Do NOT remove every return statement from a function that returns a value.
- Do NOT make the function call itself unless the original genuinely does.
- Do NOT collapse the real branch/conditional structure of the original code.
- Do NOT invent classes, types, or "recovered types from other functions"
  content that isn't evidenced by THIS function's own code — the
  whole-program/library context below is background only, never something
  to echo verbatim into your answer."""


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
        parts.append("\nRECOVERED TYPES FROM OTHER FUNCTIONS (background only — do not echo verbatim):")
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
