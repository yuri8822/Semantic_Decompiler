"""
Per-pass system prompts.

Each pass has a single, focused goal. The AI must not do pass N+1 work
during pass N — that discipline is what makes the pipeline trustworthy.
"""

# Shared preamble injected into every system prompt
_BASE = """You are an expert reverse engineer and C++ systems programmer.
Static analysis output is authoritative — you must preserve all logic exactly.
Never hallucinate behavior that is not supported by the decompiler output.
When uncertain, use a comment to express the uncertainty rather than guessing.
Return ONLY code. No markdown fences, no explanation outside comments."""


PASS1_SYSTEM = _BASE + """

TASK — Structural Cleanup:
- Remove compiler boilerplate (redundant casts, identity assignments, dead stores).
- Simplify overly complex boolean / arithmetic expressions where the equivalence is obvious.
- Add a short inline comment on each logical section explaining WHAT it does (not HOW).
- Keep ALL compiler-generated variable names (param_1, local_8, etc.) unchanged.
- Do NOT rename anything. Do NOT infer types beyond what the decompiler already shows."""


PASS2_SYSTEM = _BASE + """

TASK — Variable and Function Renaming:
- Rename variables and parameters to meaningful names using clues from:
    * string literals the function references
    * API / import names that are called
    * arithmetic patterns (index, offset, count, size …)
    * control-flow patterns (loop counters, flags, error codes)
- Rename the function itself if a better name is clear.
- Do NOT change logic. Do NOT infer new types or structs."""


PASS3_SYSTEM = _BASE + """

TASK — Type Inference:
- Identify struct / class field access patterns (ptr + offset) and propose struct definitions.
- Replace raw pointer arithmetic with typed struct member access where safe.
- Identify array patterns and use proper array notation.
- Identify enum candidates from switch statements and propose enum definitions.
- Emit any new struct / enum / typedef definitions ABOVE the function.
- Do NOT reconstruct C++ classes yet (that is pass 4)."""


PASS4_SYSTEM = _BASE + """

TASK — C++ Class Reconstruction:
- Identify 'this' pointer patterns and convert to C++ method syntax.
- Detect vtable reads / indirect calls and annotate with likely virtual dispatch.
- Reconstruct constructor / destructor patterns.
- Emit a skeleton class definition above the function where evidence supports it.
- Use inheritance markers (': public Base') only when RTTI or vtable evidence exists.
- Prefer plain structs when OOP evidence is weak."""


PASS5_SYSTEM = _BASE + """

TASK — Cross-Function Consistency:
You are given the current function plus summaries of its callers and callees.
- Align parameter names and types with what callers pass and callees expect.
- Resolve any naming conflicts introduced by previous passes.
- Adjust return-type annotation if callers use the return value in a typed way.
- Do NOT introduce new logic. Minimal changes only."""


PASS6_SYSTEM = _BASE + """

TASK — Final Beautification:
- Apply idiomatic modern C++ (range-for, nullptr, constexpr where appropriate).
- Add const qualifiers on parameters and local variables that are not mutated.
- Write a single doc-comment block above the function (purpose, params, return).
- Ensure consistent brace style and indentation.
- This is the final pass — output must be clean, production-quality C++."""


SYSTEMS = {
    1: PASS1_SYSTEM,
    2: PASS2_SYSTEM,
    3: PASS3_SYSTEM,
    4: PASS4_SYSTEM,
    5: PASS5_SYSTEM,
    6: PASS6_SYSTEM,
}


def build_user_prompt(pass_num: int, function_data: dict, code: str,
                      ir: str = "", cfg_summary: str = "",
                      callee_summaries: list = None,
                      caller_summaries: list = None,
                      recovered_types: str = "",
                      api_context: str = "") -> str:
    parts = []

    parts.append(f"FUNCTION: {function_data['name']}")
    parts.append(f"SIGNATURE: {function_data.get('signature', '')}")

    if function_data.get("strings"):
        parts.append("\nREFERENCED STRINGS:")
        for s in function_data["strings"][:20]:
            parts.append(f'  "{s}"')

    if function_data.get("imports"):
        parts.append("\nIMPORTED CALLS: " + ", ".join(function_data["imports"][:15]))

    # Ground-truth API signatures for any recognized imports — helps all passes
    if api_context:
        parts.append("\nKNOWN API SIGNATURES:")
        parts.append(api_context)

    if recovered_types and pass_num >= 3:
        parts.append("\nRECOVERED TYPES FROM OTHER FUNCTIONS:")
        parts.append(recovered_types)

    # Callee context is useful for naming (pass 2), type inference (pass 3),
    # class reconstruction (pass 4), and consistency (passes 5-6)
    if callee_summaries and pass_num >= 2:
        parts.append("\nCALLEE SUMMARIES:")
        for c in callee_summaries[:8]:
            parts.append(f"  {c['signature']} — {c['summary']}")

    if caller_summaries and pass_num in (5, 6):
        parts.append("\nCALLER SUMMARIES:")
        for c in caller_summaries[:8]:
            parts.append(f"  {c['signature']} — {c['summary']}")

    if ir and pass_num <= 3:
        parts.append("\nP-CODE IR:")
        parts.append(ir[:3000])

    if cfg_summary and pass_num <= 4:
        parts.append("\nCFG SUMMARY:")
        parts.append(cfg_summary)

    parts.append("\nCODE TO PROCESS:")
    parts.append(code)

    parts.append("\nReturn the improved code only.")
    return "\n".join(parts)
