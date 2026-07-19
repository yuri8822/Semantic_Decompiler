"""
Multi-pass AI translator.

Each pass feeds its output as input to the next, building up the
reconstruction incrementally rather than asking the AI to do
everything at once.
"""

import json
import re
from dataclasses import dataclass, field

from config import NUM_PASSES, OLLAMA_MODEL, LLM_PROVIDER
from ai.llm_client import LLMClient
from ai.prompts import SYSTEMS, build_user_prompt
from analyzer.types_db import SemanticDB


def _call_llm(client: LLMClient, system: str, user: str, pass_num: int) -> str:
    return _strip_fences(client.complete(system, user, pass_num))


def _strip_fences(text: str) -> str:
    """Remove markdown code fences the model might add despite instructions."""
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```$", "", text, flags=re.MULTILINE)
    return text.strip()


_NAME_SKIP = frozenset({
    "if", "else", "while", "for", "switch", "do", "return",
    "struct", "class", "enum", "typedef", "extern", "static", "inline",
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
    pass-6 beautification reformats a signature onto separate lines) before
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


def _is_call_site(name: str, line: str) -> bool:
    """
    True if `name(` on this line looks like an actual call, not a forward
    declaration or definition signature — i.e. not `[extern] TYPE name(...)`
    (with or without a trailing `{` or `;`).
    """
    if not re.search(r'\b' + re.escape(name) + r'\s*\(', line):
        return False
    signature = re.compile(r'^(extern\s+)?[A-Za-z_]\w*(\s*\*)*\s+' + re.escape(name) + r'\s*\(')
    return not signature.match(line.strip())


def _dropped_callees(before: str, after: str, callees: list[str]) -> set[str]:
    """
    Return callees that were actually being CALLED in `before` but no longer
    are in `after`. Checks real call sites rather than bare name presence —
    a model can delete a function's real implementation while leaving a
    stale `extern` declaration behind that reuses the same name, which would
    make the callee look "still present" even though nothing calls it
    anymore. Only checks names that were genuinely called in `before` — so if
    a callee was already renamed in a prior pass it won't have been a call
    site under the old name there either, and won't fire.
    """
    dropped = set()
    for name in callees:
        called_before = any(_is_call_site(name, line) for line in before.split("\n"))
        called_after = any(_is_call_site(name, line) for line in after.split("\n"))
        if called_before and not called_after:
            dropped.add(name)
    return dropped


# ---------------------------------------------------------------------------
# Validation layer (architecture-mission Phase 4). Generalizes the callee
# guard above into a broader set of cheap, text/regex-level checks —
# deliberately not AST diffing (out of scope for this phase; would need a
# real C++ parser). Hard-reject checks are kept to ones with low
# false-positive risk; anything murkier becomes a warning that doesn't
# block the pass, since false positives on things like "possibly invented
# API" are real and would otherwise stall legitimate passes.
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    accepted: bool
    reasons: list = field(default_factory=list)    # hard-reject reasons
    warnings: list = field(default_factory=list)   # soft, non-blocking


_RETURN_RE = re.compile(r'\breturn\b')
_BRANCH_KEYWORDS_RE = re.compile(r'\b(?:if|for|while|switch)\s*\(|\bcase\b')
_COND_BRANCHES_RE = re.compile(r'Cond\. branches:\s*(\d+)')

# Best-effort local-variable-declaration proxy: "[qualifiers] TYPE NAME [= ...];"
# with no parens between NAME and the trailing ';' (so it doesn't match
# function declarations/definitions, which have a parameter list there).
# Known imprecise: doesn't handle comma-separated multi-declarators
# ("int a, b;" undercounts) — acceptable for a cheap regex-level check,
# same spirit as this codebase's other approximate heuristics (e.g.
# cfg_builder.py's naive cyclomatic-complexity formula).
_LOCAL_DECL_RE = re.compile(
    r'^(?:const\s+|static\s+|volatile\s+|constexpr\s+)*'
    r'[A-Za-z_]\w*(?:::\w+)*(?:\s*[\*&])*\s+[A-Za-z_]\w*\s*(?:=[^;()]*)?;$'
)


def _count_branches(code: str) -> int:
    return len(_BRANCH_KEYWORDS_RE.findall(code))


def _count_local_decls(code: str) -> int:
    count = 0
    for line in code.split("\n"):
        s = line.strip()
        if not s or re.match(r'^(struct|class|enum|typedef|extern|return)\b', s):
            continue
        if _LOCAL_DECL_RE.match(s):
            count += 1
    return count


def _call_site_names(code: str) -> set:
    """Every identifier that appears as a genuine call site somewhere in code."""
    names = set()
    for line in code.split("\n"):
        for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', line):
            candidate = m.group(1)
            if candidate in _NAME_SKIP:
                continue
            if _is_call_site(candidate, line):
                names.add(candidate)
    return names


def validate_transition(prev_code: str, new_code: str, callees: list,
                         locked_name: str, cfg_summary: str, pass_num: int) -> ValidationResult:
    """
    Runs every cheap correctness check on one pass's proposed transition
    (prev_code -> new_code). Includes the original callee guard unchanged.
    """
    reasons = []
    warnings = []

    # Callee guard (pre-existing, unchanged) — hard reject.
    if pass_num >= 3 and callees:
        dropped = _dropped_callees(prev_code, new_code, callees)
        if dropped:
            reasons.append(f"dropped call(s) to: {', '.join(sorted(dropped))}")

    # Return-statement-dropped — hard reject only on >=1 -> 0, not exact count
    # (collapsing duplicate early-returns into one guard clause is legitimate).
    prev_returns = len(_RETURN_RE.findall(prev_code))
    new_returns = len(_RETURN_RE.findall(new_code))
    if prev_returns >= 1 and new_returns == 0:
        reasons.append("all return statements were removed")

    # Branch-count vs. the original (pre-AI) CFG analysis — hard reject only
    # on a large drop (>30%), not exact match; beautification legitimately
    # rewrites if/else into other equivalent forms sometimes.
    m = _COND_BRANCHES_RE.search(cfg_summary or "")
    if m:
        cfg_branches = int(m.group(1))
        if cfg_branches > 0:
            new_branches = _count_branches(new_code)
            if new_branches < cfg_branches * 0.7:
                reasons.append(
                    f"branch count dropped from ~{cfg_branches} (per CFG analysis) "
                    f"to {new_branches} in the output"
                )

    # Self-recursion — hard reject, but only newly-introduced recursion, so
    # a function that's genuinely already (correctly) recursive isn't
    # falsely flagged every pass.
    if locked_name:
        was_recursive = any(_is_call_site(locked_name, line) for line in prev_code.split("\n"))
        is_recursive = any(_is_call_site(locked_name, line) for line in new_code.split("\n"))
        if is_recursive and not was_recursive:
            reasons.append(f"function calls itself ('{locked_name}') — likely hallucinated self-recursion")

    # Unknown API/global — soft warning only. False positives are real here
    # (legitimate local helper names introduced during class recon), so this
    # never blocks a pass.
    known = set(callees) | _call_site_names(prev_code)
    newly_called = _call_site_names(new_code) - known
    if newly_called:
        warnings.append(f"possibly invented call(s) not seen before this pass: {', '.join(sorted(newly_called))}")

    # Stack-variable-count — pass >=3 only (pass 1's legitimate dead-store
    # removal would otherwise false-positive). Hard reject on a large drop.
    if pass_num >= 3:
        prev_locals = _count_local_decls(prev_code)
        new_locals = _count_local_decls(new_code)
        if prev_locals >= 2 and new_locals < prev_locals * 0.5:
            reasons.append(f"local variable count dropped from {prev_locals} to {new_locals}")

    return ValidationResult(accepted=not reasons, reasons=reasons, warnings=warnings)


_TYPE_START_RE = re.compile(
    r'^(struct|class|enum(?:\s+class)?)\s+(\w+)\s*(?::[^{;]*)?\{',
    re.MULTILINE
)


def _format_deterministic_facts(facts: list[dict]) -> str:
    """
    Render architecture-mission Phase 2's deterministic evidence
    (analyzer/cfg_builder.py's analyze_deterministic) for one function's
    entity into prompt-ready text. Pure static analysis, not AI-inferred.
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
    Render architecture-mission Phase 2's STL/library detections
    (analyzer/library_signatures.py) this function references, via the
    knowledge-graph `references` relationships recorded in main.py's seed step.
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
    just this function's own direct callers/callees (mission item #1,
    "whole-program reconstruction").
    """
    entities = db.get_entities_by_kind(binary_name, "type")
    keys = sorted({e["key"] for e in entities})
    if not keys:
        return ""
    return "Known types/classes already identified elsewhere in this binary:\n  " + ", ".join(keys[:30])


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


class MultiPassTranslator:
    def __init__(self, db: SemanticDB, binary_name: str, num_passes: int = NUM_PASSES,
                 provider: str = LLM_PROVIDER, ollama_model: str = OLLAMA_MODEL,
                 restart: bool = False):
        self.db = db
        self.binary_name = binary_name
        self.num_passes = min(num_passes, NUM_PASSES)
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

        # Seed: start from raw decompiler output
        current_code = function_data.get("decompiled", "")
        if not current_code.strip():
            return f"// {name}: decompiler produced no output\nvoid {name}() {{}}\n"

        # Pass-level resume: a prior attempt by this SAME provider may have
        # completed some passes before being interrupted. A stored pass
        # belonging to a DIFFERENT (or no) provider must never be reused —
        # only compare before overwriting the provider marker below.
        # `--restart` forces same_provider False regardless of what's stored,
        # so this function always starts clean from pass 1.
        existing = self.db.get_function(self.binary_name, address)
        same_provider = (
            bool(existing) and existing.get("provider") == self.provider and not self.restart
        )

        # Switching providers (or a brand-new function): any stale results
        # from a different provider must be cleared BEFORE marking the new
        # provider. Otherwise, an interruption right after the switch (before
        # this provider has produced anything) could leave `provider` set to
        # the new one while `final_cpp` still holds the old provider's
        # complete result — which is_complete_for_provider would then wrongly
        # read as "already done" on a later resumed run.
        if not same_provider:
            self.db.clear_pass_data(self.binary_name, address)

        # Mark which provider is (re)producing this function's results, so a
        # resumed run can tell "already done" from "done by someone else"
        self.db.set_provider(self.binary_name, address, self.provider)

        ai_name = ""  # locked in after pass 2
        start_pass = 1

        if same_provider:
            for pass_num in range(1, self.num_passes + 1):
                stored = existing.get(f"pass{pass_num}_output") or ""
                if not stored:
                    break
                current_code = stored
                start_pass = pass_num + 1
                if pass_num == 2:
                    ai_name = extract_function_name(current_code, fallback="")
                    if ai_name and ai_name != name:
                        self.db.set_ai_name(self.binary_name, address, ai_name)  # idempotent if already persisted
                if pass_num == 3:
                    self._harvest_types(current_code, address)  # idempotent
            if start_pass > 1:
                print(f"[translator] Resuming {name} from pass {start_pass} "
                      f"(passes 1-{start_pass - 1} already done by {self.provider})")

        # Fetch shared context (types discovered so far, neighbour summaries)
        recovered_types = self.db.get_types_for_context()
        callee_summaries = self.db.get_callee_summaries(self.binary_name, address)
        caller_summaries = self.db.get_caller_summaries(self.binary_name, name)
        callees = function_data.get("callees", [])

        # Architecture-mission Phase 3: evidence-based, whole-program-aware
        # prompting. Pulls from the knowledge graph populated in Phase 1/2 —
        # purely additive prompt CONTENT, no change to the pass loop, name
        # lock, or callee guard below.
        entity_id = self.db.get_entity_id(self.binary_name, "function", address)
        deterministic_evidence = (
            _format_deterministic_facts(self.db.get_entity_facts(entity_id))
            if entity_id else ""
        )
        library_hints = (
            _format_library_hints(self.db, entity_id) if entity_id else ""
        )
        whole_program_context = _format_whole_program_context(self.db, self.binary_name)

        for pass_num in range(start_pass, self.num_passes + 1):
            prev_code = current_code

            system = SYSTEMS[pass_num]
            user = build_user_prompt(
                pass_num=pass_num,
                function_data=function_data,
                code=current_code,
                ir=ir,
                cfg_summary=cfg_summary,
                callee_summaries=callee_summaries,
                caller_summaries=caller_summaries,
                recovered_types=recovered_types,
                api_context=api_context,
                ai_name=ai_name,
                deterministic_evidence=deterministic_evidence,
                library_hints=library_hints,
                whole_program_context=whole_program_context,
            )

            new_code = _call_llm(self._llm, system, user, pass_num)

            # Validation layer (architecture-mission Phase 4): run every
            # cheap correctness check (includes the original callee guard).
            # On failure, one bounded retry with explicit feedback about
            # what was wrong, restarting from prev_code (never compounding
            # on the rejected output); if the retry also fails, fall back
            # to the original revert-to-prev_code behavior.
            locked_name = ai_name or name
            result = validate_transition(prev_code, new_code, callees, locked_name, cfg_summary, pass_num)

            if not result.accepted:
                print(f"[translator] Pass {pass_num} rejected for {name}: "
                      f"{'; '.join(result.reasons)} — retrying once with feedback")
                feedback = (
                    "\n\nVALIDATION FAILURE — your previous attempt was rejected for:\n"
                    + "\n".join(f"  - {r}" for r in result.reasons)
                    + "\nRegenerate from the CODE TO PROCESS above, preserving everything "
                      "the failure(s) above call out. Do not repeat the same mistake."
                )
                retry_code = _call_llm(self._llm, system, user + feedback, pass_num)
                retry_result = validate_transition(prev_code, retry_code, callees, locked_name, cfg_summary, pass_num)
                if retry_result.accepted:
                    new_code, result = retry_code, retry_result
                else:
                    print(f"[translator] Pass {pass_num} retry also rejected for {name}: "
                          f"{'; '.join(retry_result.reasons)} — reverting to previous pass's output")
                    new_code = prev_code  # fall back to the pre-existing revert behavior

            if result.warnings:
                print(f"[translator] Pass {pass_num} warning for {name}: {'; '.join(result.warnings)}")

            current_code = new_code
            self.db.set_pass_output(self.binary_name, address, pass_num, current_code)

            # Fix 2 — name lock: after pass 2 (renaming), capture the AI's chosen
            # function name and store it so later passes can't silently revert it.
            if pass_num == 2:
                ai_name = extract_function_name(current_code, fallback="")
                if ai_name and ai_name != name:
                    self.db.set_ai_name(self.binary_name, address, ai_name)

            # After pass 3, extract any new type definitions and store them
            if pass_num == 3:
                self._harvest_types(current_code, address)
                recovered_types = self.db.get_types_for_context()

        self.db.set_final_cpp(self.binary_name, address, current_code)

        # Generate a one-line summary for the call-graph context of other functions
        display_name = ai_name or name
        summary = self._summarise(display_name, current_code)
        self.db.set_summary(self.binary_name, address, summary)

        return current_code

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _harvest_types(self, code: str, source_addr: str):
        """
        Extract struct/enum/class definitions from pass-3 output and store
        them. Uses real brace-depth matching rather than a single-level
        regex (`\\{[^}]*\\}`) — a class with an inline method body has its
        own nested braces, which the old regex would truncate at (the
        method's closing brace, not the type's own), storing a broken
        fragment as if it were the complete type. Same underlying bug as
        the one found and fixed in output/writer.py's header generation.
        """
        for m in _TYPE_START_RE.finditer(code):
            open_brace = m.end() - 1
            close_brace = _matching_brace(code, open_brace)
            if close_brace is None:
                continue  # unbalanced in the source — skip rather than store a truncated fragment
            tail = re.match(r'\s*;', code[close_brace + 1: close_brace + 21])
            if not tail:
                continue  # not actually followed by `;` — not a complete definition
            kind = m.group(1).split()[0]  # 'struct', 'class', 'enum'
            name = m.group(2)
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
