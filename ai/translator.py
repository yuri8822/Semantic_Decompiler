"""
Single-pass AI translator, plus a second reviewer agent that checks the
first agent's own output against the original Ghidra decompiled code. The
context that used to be spread across 6 passes (deterministic evidence,
library hints, whole-program context, callee/caller summaries, known-API
signatures, p-code IR, CFG summary) is gathered once and reused for both
agents; see build_review_prompt()/REVIEWER_SYSTEM_PROMPT in ai/prompts.py.
"""

import json
import re

from config import OLLAMA_MODEL, LLM_PROVIDER, MAX_REVIEW_ROUNDS
from ai.llm_client import LLMClient
from ai.prompts import SYSTEM_PROMPT, build_user_prompt, REVIEWER_SYSTEM_PROMPT, build_review_prompt
from analyzer.types_db import SemanticDB


def _call_llm(client: LLMClient, system: str, user: str, pass_num: int) -> str:
    return _strip_fences(client.complete(system, user, pass_num))


def _strip_fences(text: str) -> str:
    """
    Remove markdown code fences the model might add despite instructions.
    Tolerates incidental leading/trailing whitespace around the fence line
    itself (e.g. " ```", a single space before the backticks) -- anchoring
    directly to "^```" with no slack there let a real, observed case slip
    through unstripped and land verbatim in the output.
    """
    text = re.sub(r"^[ \t]*```[a-zA-Z]*[ \t]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*```[ \t]*$", "", text, flags=re.MULTILINE)
    return text.strip()


_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)\b", re.IGNORECASE)


def _parse_verdict(text: str) -> tuple[str, str]:
    """
    (verdict, issues) from the reviewer's response. Matched via an explicit
    "VERDICT: PASS|FAIL" line rather than scanning the prose for a bare
    word like "approved" -- free text can say something like "I'd approve
    this if X were fixed" without meaning to pass it, so only the fixed-
    format line is trusted as the actual signal. If the reviewer doesn't
    produce that line at all, the response is treated as FAIL (the raw
    text becomes the "issues") rather than assumed to be a pass.
    """
    m = _VERDICT_RE.search(text)
    if not m:
        return "FAIL", text.strip()
    return m.group(1).upper(), text[m.end():].strip()


_NAME_SKIP = frozenset({
    "if", "else", "while", "for", "switch", "do", "return",
    "struct", "class", "enum", "typedef", "extern", "static", "inline",
    "decltype", "sizeof", "typename", "noexcept", "alignof", "requires",
    "const", "constexpr", "consteval",
})


def extract_function_name(code: str, fallback: str) -> str:
    """
    Extract the function name the AI chose in its output.
    Looks for the first *top-level* function definition line (has parens,
    followed by {, and not nested inside any braces — e.g. a class body).
    Tracks multi-line /* ... */ comments so a parenthesized word inside a
    doc comment can't be mistaken for the function's own name, and tracks
    brace depth so a member declared inside an injected/leaked type
    definition can't be mistaken for the actual function being translated
    either — a real observed bug: a leaked "RECOVERED TYPES FROM OTHER
    FUNCTIONS" block ahead of the real function caused `virtual ~Bishop()
    {}` (a destructor nested inside `class Bishop { ... }`) to be matched
    as if "Bishop" were the real, top-level function's name.

    Also merges a parameter list split across multiple lines (common after
    beautification reformats a signature onto separate lines) before
    checking for the opening brace — a one-line-only lookahead used to miss
    the rename entirely whenever a signature spanned more than two lines,
    silently falling back to the original Ghidra name instead.
    """
    in_comment = False
    depth = 0
    lines = code.split("\n")
    n = len(lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if in_comment:
            if "*/" in s:
                in_comment = False
            i += 1
            continue
        if s.startswith("/*"):
            if "*/" not in s:
                in_comment = True
            i += 1
            continue
        if not s or s.startswith("//") or s.startswith("#"):
            i += 1
            continue

        is_type_decl = bool(re.match(r'^(struct|class|enum|typedef|extern)\b', s))
        has_paren = "(" in s

        # Merge continuation lines while a parameter list is still open
        # (unbalanced parens) — the opening { can be several lines past
        # where the parens finally close. Capped so malformed/unbalanced
        # text can't spin: no real signature is anywhere near this long.
        j = i
        merged = s
        paren_depth = merged.count("(") - merged.count(")")
        MAX_CONTINUATION_LINES = 20
        while has_paren and paren_depth > 0 and j + 1 < n and (j - i) < MAX_CONTINUATION_LINES:
            j += 1
            nxt = lines[j].strip()
            merged += " " + nxt
            paren_depth += nxt.count("(") - nxt.count(")")

        # Must be a definition: has { in the merged signature, or on the
        # very next line after it closes
        has_brace = "{" in merged or (
            j + 1 < n and lines[j + 1].strip().startswith("{")
        )
        sig = merged.split("{")[0].rstrip() if has_brace else merged
        is_forward_decl = sig.endswith(";")  # ends with ; but no { — not a definition

        if depth == 0 and not is_type_decl and has_paren and has_brace and not is_forward_decl:
            before_paren = sig.split("(")[0].rstrip()
            m = re.search(r'\b([A-Za-z_]\w*)\s*$', before_paren)
            if m and m.group(1) not in _NAME_SKIP:
                return m.group(1)

        depth += merged.count("{") - merged.count("}")
        i = j + 1
    return fallback


_TYPE_START_RE = re.compile(
    r'^(?:struct|class|enum(?:\s+class)?|typedef\s+struct|typedef\s+enum)'
    r'\s+(?P<name>\w+)\b[^{;]*\{',
    re.MULTILINE
)


def _matching_brace(code: str, open_pos: int):
    """Index of the `}` that closes the `{` at `open_pos`, or None if unbalanced."""
    depth = 0
    for i in range(open_pos, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _format_deterministic_facts(facts: list[dict]) -> str:
    """
    Render pure static-analysis evidence (analyzer/cfg_builder.py's
    analyze_deterministic) for one function's entity into prompt-ready
    text. Ground truth, not AI-inferred.
    """
    lines = []
    for f in facts:
        ftype, value = f["fact_type"], f["value"]
        if ftype == "calling_convention":
            lines.append(f"  Calling convention: {value}")
        elif ftype == "natural_loops":
            loops = json.loads(value)
            lines.append(f"  Natural loops detected (dominator-based): {len(loops)}")
        elif ftype == "propagated_constants":
            consts = json.loads(value)
            shown = ", ".join(f"{c['varnode']}={c['value']}" for c in consts[:10])
            lines.append(f"  Propagated constants ({len(consts)} total): {shown}")
        elif ftype == "alias_hints":
            alias = json.loads(value)
            if alias.get("distinct_stack_locals"):
                lines.append("  Guaranteed-distinct stack locals: "
                              + ", ".join(alias["distinct_stack_locals"]))
            if alias.get("distinct_alloc_call_sites"):
                lines.append(f"  Distinct heap allocation call sites: "
                              f"{alias['distinct_alloc_call_sites']}")
    return "\n".join(lines)


def _format_library_hints(db: SemanticDB, entity_id: int) -> str:
    """
    Render STL/library detections (analyzer/library_signatures.py) this
    function references, via the knowledge-graph `references`
    relationships recorded in main.py's seed step.
    """
    lines = []
    for rel in db.get_relationships(entity_id, direction="out", rel_type="references"):
        type_entity = db.get_entity(rel["dst_entity_id"])
        if not type_entity:
            continue
        fact = db.get_current_fact(rel["dst_entity_id"], "library")
        library = fact["value"] if fact else "?"
        lines.append(f"  {type_entity['key']} (library: {library}) — "
                      f"use the real type, do not reinvent its internals.")
    return "\n".join(lines)


def _format_whole_program_context(db: SemanticDB, binary_name: str) -> str:
    """
    Known types/classes already identified elsewhere in this binary — not
    just this function's own direct callers/callees.
    """
    entities = db.get_entities_by_kind(binary_name, "type")
    keys = sorted({e["key"] for e in entities})
    if not keys:
        return ""
    return "Known types/classes already identified elsewhere in this binary:\n  " + ", ".join(keys[:30])


class FunctionTranslator:
    def __init__(self, db: SemanticDB, binary_name: str,
                 provider: str = LLM_PROVIDER, ollama_model: str = OLLAMA_MODEL,
                 restart: bool = False):
        self.db = db
        self.binary_name = binary_name
        self.provider = provider.lower()
        self.restart = restart
        self._llm = LLMClient(provider=provider, ollama_model=ollama_model)

    def translate(
        self,
        function_data: dict,
        ir: str = "",
        cfg_summary: str = "",
        api_context: str = "",
    ) -> str:
        address = function_data["address"]
        name = function_data["name"]

        decompiled = function_data.get("decompiled", "")
        if not decompiled.strip():
            return f"// {name}: decompiler produced no output\nvoid {name}() {{}}\n"

        # A prior attempt by a DIFFERENT provider must never be reused —
        # clear it before marking the new provider, so an interruption
        # right after the switch can't leave `provider` set to the new one
        # while `final_cpp` still holds the old provider's result (which
        # is_complete_for_provider would then wrongly read as "done").
        existing = self.db.get_function(self.binary_name, address)
        same_provider = (
            bool(existing) and existing.get("provider") == self.provider and not self.restart
        )
        if not same_provider:
            self.db.clear_result(self.binary_name, address)
        self.db.set_provider(self.binary_name, address, self.provider)

        # Rich context: types discovered so far, neighbour summaries, and
        # the whole-program knowledge graph (entities/facts/relationships).
        recovered_types = self.db.get_types_for_context()
        callee_summaries = self.db.get_callee_summaries(self.binary_name, address)
        caller_summaries = self.db.get_caller_summaries(self.binary_name, name)

        entity_id = self.db.get_entity_id(self.binary_name, "function", address)
        deterministic_evidence = (
            _format_deterministic_facts(self.db.get_entity_facts(entity_id))
            if entity_id else ""
        )
        library_hints = (
            _format_library_hints(self.db, entity_id) if entity_id else ""
        )
        whole_program_context = _format_whole_program_context(self.db, self.binary_name)

        user = build_user_prompt(
            function_data=function_data,
            ir=ir,
            cfg_summary=cfg_summary,
            api_context=api_context,
            deterministic_evidence=deterministic_evidence,
            library_hints=library_hints,
            whole_program_context=whole_program_context,
            recovered_types=recovered_types,
            callee_summaries=callee_summaries,
            caller_summaries=caller_summaries,
        )

        # pass_num=4 selects whichever "heavy" tier a provider splits on
        # (e.g. Anthropic uses claude-opus-4-8 for passes 3-4) — this one
        # call is doing all the reasoning a 6-pass pipeline used to split
        # up, so it always gets the strongest available tier.
        final_code = _call_llm(self._llm, SYSTEM_PROMPT, user, pass_num=4)

        final_code, review_status = self._review_loop(
            function_data, final_code, api_context, deterministic_evidence,
            library_hints, whole_program_context, recovered_types,
            callee_summaries, caller_summaries, ir, cfg_summary,
        )

        self.db.set_final_cpp(self.binary_name, address, final_code)
        self.db.set_review_status(self.binary_name, address, review_status)

        ai_name = extract_function_name(final_code, fallback="")
        if ai_name and ai_name != name:
            self.db.set_ai_name(self.binary_name, address, ai_name)

        self._harvest_types(final_code, address)

        display_name = ai_name or name
        summary = self._summarise(display_name, final_code)
        self.db.set_summary(self.binary_name, address, summary)

        return final_code

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _review_loop(self, function_data, code, api_context, deterministic_evidence,
                      library_hints, whole_program_context, recovered_types,
                      callee_summaries, caller_summaries, ir, cfg_summary) -> tuple:
        """
        Second-agent review: a separate reviewer prompt (REVIEWER_SYSTEM_PROMPT)
        checks `code` against function_data['decompiled'] — the original
        Ghidra output, ground truth for logic/structure — and either passes
        it or returns concrete issues, which get fed back into another
        refactor attempt via build_user_prompt's previous_attempt/
        review_feedback. Bounded by MAX_REVIEW_ROUNDS so a stubborn
        disagreement can't loop forever: whatever the last attempt produced
        ships either way, the returned status just says whether the
        reviewer actually agreed to it.

        MAX_REVIEW_ROUNDS = 0 disables the review loop entirely -- `code`
        ships as-is with no reviewer call at all, not even one.
        """
        if MAX_REVIEW_ROUNDS <= 0:
            return code, "skipped"

        for round_num in range(MAX_REVIEW_ROUNDS):
            review_text = self._llm.complete(
                REVIEWER_SYSTEM_PROMPT,
                build_review_prompt(
                    function_data=function_data,
                    refactored_code=code,
                    api_context=api_context,
                    deterministic_evidence=deterministic_evidence,
                    library_hints=library_hints,
                    recovered_types=recovered_types,
                    callee_summaries=callee_summaries,
                    caller_summaries=caller_summaries,
                ),
                pass_num=4,
            )
            verdict, issues = _parse_verdict(review_text)
            if verdict == "PASS":
                return code, "passed"
            if round_num == MAX_REVIEW_ROUNDS - 1:
                return code, "unresolved"

            user = build_user_prompt(
                function_data=function_data,
                ir=ir,
                cfg_summary=cfg_summary,
                api_context=api_context,
                deterministic_evidence=deterministic_evidence,
                library_hints=library_hints,
                whole_program_context=whole_program_context,
                recovered_types=recovered_types,
                callee_summaries=callee_summaries,
                caller_summaries=caller_summaries,
                previous_attempt=code,
                review_feedback=issues,
            )
            code = _call_llm(self._llm, SYSTEM_PROMPT, user, pass_num=4)

    def _harvest_types(self, code: str, source_addr: str):
        """
        Extract struct/enum/class/typedef definitions from the final
        output and store them. Uses real brace-depth matching rather than
        a single-level regex (`\\{[^}]*\\}`) — a class with an inline
        method body has its own nested braces, which the old regex would
        truncate at (the method's closing brace, not the type's own),
        storing a broken fragment as if it were the complete type. Same
        underlying bug as the one found and fixed in output/writer.py's
        header generation; the two share the same regex shape for this
        exact reason.
        """
        for m in _TYPE_START_RE.finditer(code):
            open_brace = m.end() - 1
            close_brace = _matching_brace(code, open_brace)
            if close_brace is None:
                continue  # unbalanced in the source — skip rather than store a truncated fragment
            tail = re.match(r'\s*(\w*)\s*;', code[close_brace + 1: close_brace + 41])
            if not tail:
                continue  # not actually followed by `;` — not a complete definition
            kind = m.group(0).split()[0] if not m.group(0).startswith("typedef") else "typedef"
            name = m.group("name")
            definition = code[m.start():close_brace + 1 + tail.end()].strip()
            self.db.upsert_type(
                name=name,
                kind=kind,
                definition=definition,
                source_function=source_addr,
            )

    def _summarise(self, name: str, code: str) -> str:
        """Ask the model for a one-line summary; used as context for neighbours."""
        system = "You are a terse reverse engineering assistant. Return only the one-sentence summary."
        user = (
            f"Summarise the purpose of this C++ function in ONE sentence (max 20 words).\n"
            f"Function name: {name}\n\n{code[:1500]}"
        )
        # Pass pass_num=1 so Anthropic uses the fast model for this lightweight call
        return self._llm.complete(system, user, pass_num=1).strip()
