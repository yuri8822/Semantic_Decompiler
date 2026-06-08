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

        for pass_num in range(1, self.num_passes + 1):
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
            )

            current_code = _call_llm(self._llm, system, user, pass_num)
            self.db.set_pass_output(address, pass_num, current_code)

            # After pass 3, extract any new type definitions and store them
            if pass_num == 3:
                self._harvest_types(current_code, address)
                recovered_types = self.db.get_types_for_context()

        self.db.set_final_cpp(address, current_code)

        # Generate a one-line summary for the call-graph context of other functions
        summary = self._summarise(name, current_code)
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
