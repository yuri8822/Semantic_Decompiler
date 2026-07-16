"""
Multi-pass AI translator.

Each pass feeds its output as input to the next, building up the
reconstruction incrementally rather than asking the AI to do
everything at once.
"""

import re

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
    Looks for the first function definition line (has parens, followed by {).
    Tracks multi-line /* ... */ comments so a parenthesized word inside a
    doc comment can't be mistaken for the function's own name.
    """
    in_comment = False
    lines = code.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if in_comment:
            if "*/" in s:
                in_comment = False
            continue
        if s.startswith("/*"):
            if "*/" not in s:
                in_comment = True
            continue
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        if re.match(r'^(struct|class|enum|typedef|extern)\b', s):
            continue
        if "(" not in s:
            continue
        # Must be a definition: has { on this line or the very next
        has_brace = "{" in s or (
            i + 1 < len(lines) and lines[i + 1].strip().startswith("{")
        )
        if not has_brace:
            continue
        # Skip forward declarations (ends with ; but no {)
        sig = s.split("{")[0].rstrip()
        if sig.endswith(";"):
            continue
        before_paren = sig.split("(")[0].rstrip()
        m = re.search(r'\b([A-Za-z_]\w*)\s*$', before_paren)
        if m and m.group(1) not in _NAME_SKIP:
            return m.group(1)
    return fallback


def _dropped_callees(before: str, after: str, callees: list[str]) -> set[str]:
    """
    Return callees that were present in `before` but are absent in `after`.
    Only checks names that actually appeared in `before` — so if a callee was
    already renamed in a prior pass it won't appear in `before` and won't fire.
    """
    return {
        name for name in callees
        if re.search(r'\b' + re.escape(name) + r'\b', before)
        and not re.search(r'\b' + re.escape(name) + r'\b', after)
    }


class MultiPassTranslator:
    def __init__(self, db: SemanticDB, num_passes: int = NUM_PASSES,
                 provider: str = LLM_PROVIDER, ollama_model: str = OLLAMA_MODEL):
        self.db = db
        self.num_passes = min(num_passes, NUM_PASSES)
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

        # Fetch shared context (types discovered so far, neighbour summaries)
        recovered_types = self.db.get_types_for_context()
        callee_summaries = self.db.get_callee_summaries(address)
        caller_summaries = self.db.get_caller_summaries(name)
        callees = function_data.get("callees", [])

        ai_name = ""  # locked in after pass 2

        for pass_num in range(1, self.num_passes + 1):
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
            )

            new_code = _call_llm(self._llm, system, user, pass_num)

            # Fix 1 — callee guard: revert if any known callee was silently dropped.
            # Only applied from pass 3 onwards (pass 2 legitimately renames calls).
            if pass_num >= 3 and callees:
                dropped = _dropped_callees(prev_code, new_code, callees)
                if dropped:
                    new_code = prev_code  # reject this pass, keep previous output

            current_code = new_code
            self.db.set_pass_output(address, pass_num, current_code)

            # Fix 2 — name lock: after pass 2 (renaming), capture the AI's chosen
            # function name and store it so later passes can't silently revert it.
            if pass_num == 2:
                ai_name = extract_function_name(current_code, fallback="")
                if ai_name and ai_name != name:
                    self.db.set_ai_name(address, ai_name)

            # After pass 3, extract any new type definitions and store them
            if pass_num == 3:
                self._harvest_types(current_code, address)
                recovered_types = self.db.get_types_for_context()

        self.db.set_final_cpp(address, current_code)

        # Generate a one-line summary for the call-graph context of other functions
        display_name = ai_name or name
        summary = self._summarise(display_name, current_code)
        self.db.set_summary(address, summary)

        return current_code

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _harvest_types(self, code: str, source_addr: str):
        """Extract struct/enum/class definitions from pass-3 output and store them."""
        # Match top-level struct / class / enum blocks
        pattern = re.compile(
            r'^(struct|class|enum(?:\s+class)?)\s+(\w+)\s*(?::[^{]*)?\{[^}]*\}\s*;',
            re.MULTILINE | re.DOTALL
        )
        for m in pattern.finditer(code):
            kind = m.group(1).split()[0]  # 'struct', 'class', 'enum'
            name = m.group(2)
            definition = m.group(0)
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
