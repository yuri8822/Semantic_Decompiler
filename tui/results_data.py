"""
Loads per-function before/after data for the Results tab. Pure data access,
no Textual import — reuses the same loaders the pipeline itself uses, no new
persistence: the Ghidra JSON export for "before" text, semantic.db for
"after" (AI-recovered) text.
"""

from dataclasses import dataclass
from pathlib import Path

from analyzer.parse_output import load_analysis
from analyzer.types_db import SemanticDB
from config import DB_PATH
from pipeline import resolve_json_path


@dataclass
class FunctionRow:
    address: str
    ghidra_name: str
    ai_name: str
    translated: bool
    decompiled: str
    final_cpp: str


def load_results(binary_path: str) -> list[FunctionRow]:
    path = Path(binary_path)
    json_path = resolve_json_path(path)
    if not Path(json_path).exists():
        raise FileNotFoundError(f"No Ghidra export found for {binary_path} — run it first")

    functions = load_analysis(json_path)
    binary_name = path.stem
    db = SemanticDB(DB_PATH)
    db.init()

    rows = []
    for fn in functions:
        cached = db.get_function(binary_name, fn.address) or {}
        final_cpp = cached.get("final_cpp") or ""
        rows.append(FunctionRow(
            address=fn.address,
            ghidra_name=fn.name,
            ai_name=cached.get("ai_name") or "",
            translated=bool(final_cpp),
            decompiled=fn.decompiled,
            final_cpp=final_cpp or "// not yet translated",
        ))
    return rows
