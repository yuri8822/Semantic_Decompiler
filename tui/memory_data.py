"""
Data access for the Memory tab — read-only browser over semantic.db's tables
(functions, recovered_types, variable_names, call_graph). Pure data access,
no Textual import, same separation as results_data.py.
"""

from analyzer.types_db import SemanticDB
from config import DB_PATH

FUNCTIONS = "functions"
TYPES = "types"
VARIABLES = "variables"
CALL_GRAPH = "call_graph"

# Order controls the table-select dropdown.
TABLE_LABELS = {
    FUNCTIONS: "Functions",
    TYPES: "Recovered Types",
    VARIABLES: "Variable Names",
    CALL_GRAPH: "Call Graph",
}

# (header, row dict key) pairs shown as DataTable columns, per table.
DISPLAY_COLUMNS = {
    FUNCTIONS: [
        ("Binary", "binary"), ("Address", "address"), ("Ghidra Name", "name"),
        ("AI Name", "ai_name"), ("Provider", "provider"),
        ("Translated", "translated"), ("Analyzed At", "analyzed_at"),
    ],
    TYPES: [
        ("Name", "name"), ("Kind", "kind"),
        ("Source Function", "source_function"), ("Confidence", "confidence"),
    ],
    VARIABLES: [
        ("Function Addr", "function_addr"), ("Original Name", "original_name"),
        ("Inferred Name", "inferred_name"), ("Inferred Type", "inferred_type"),
    ],
    CALL_GRAPH: [
        ("Binary", "binary"), ("Caller Addr", "caller_addr"), ("Callee Name", "callee_name"),
    ],
}

# Row dict key holding a large free-text field worth showing in a detail
# pane, and the syntax lexer to render it with. None if a table has none.
DETAIL_FIELD = {
    FUNCTIONS: ("summary", "Summary", None),
    TYPES: ("definition", "Definition", "cpp"),
    VARIABLES: (None, None, None),
    CALL_GRAPH: (None, None, None),
}

# Row dict key(s) that make a row unique within a table, used as the
# DataTable row key so a selection can be mapped back to its full row.
ROW_KEY_FIELDS = {
    FUNCTIONS: ("binary", "address"),
    TYPES: ("name",),
    VARIABLES: ("function_addr", "original_name"),
    CALL_GRAPH: ("binary", "caller_addr", "callee_name"),
}

# Row dict key naming which binary a row belongs to, for the binary filter.
# None if the table isn't binary-scoped.
BINARY_FIELD = {
    FUNCTIONS: "binary",
    TYPES: None,
    VARIABLES: None,
    CALL_GRAPH: "binary",
}


def row_key(table: str, row: dict) -> str:
    return "|".join(str(row.get(f, "")) for f in ROW_KEY_FIELDS[table])


def load_rows(table: str) -> list[dict]:
    db = SemanticDB(DB_PATH)
    db.init()
    if table == FUNCTIONS:
        rows = db.get_all_functions()
        for r in rows:
            r["translated"] = "yes" if r.get("final_cpp") else "no"
        return rows
    if table == TYPES:
        return db.get_all_types()
    if table == VARIABLES:
        return db.get_all_variable_names()
    if table == CALL_GRAPH:
        return db.get_all_call_graph()
    raise ValueError(f"Unknown table: {table}")
