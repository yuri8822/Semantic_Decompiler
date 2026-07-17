"""
Semantic memory database.

Stores everything the pipeline learns across functions so later passes
and later runs can build on prior discoveries rather than starting blind.

Schema overview
---------------
functions        — per-function analysis state, one row per pass output
recovered_types  — inferred structs / classes / enums / typedefs
variable_names   — inferred names for compiler-generated variable IDs
call_graph       — caller→callee edges (populated from Ghidra export)
"""

import sqlite3
from contextlib import contextmanager
from typing import Optional


class SemanticDB:
    # Additive columns on `functions` — the single source of truth for both
    # CREATE TABLE (new databases) and the migration below (existing ones).
    # `id` is intentionally excluded; it's the primary key, defined separately.
    _FUNCTIONS_COLUMNS = {
        "name":         "TEXT    NOT NULL",
        "address":      "TEXT    NOT NULL UNIQUE",
        "signature":    "TEXT    DEFAULT ''",
        "ai_name":      "TEXT    DEFAULT ''",  # name chosen by AI in pass 2 (locked in)
        "provider":     "TEXT    DEFAULT ''",  # which LLM provider produced the current results
        "summary":      "TEXT    DEFAULT ''",  # one-line AI-generated description
        "pass1_output": "TEXT    DEFAULT ''",
        "pass2_output": "TEXT    DEFAULT ''",
        "pass3_output": "TEXT    DEFAULT ''",
        "pass4_output": "TEXT    DEFAULT ''",
        "pass5_output": "TEXT    DEFAULT ''",
        "pass6_output": "TEXT    DEFAULT ''",
        "final_cpp":    "TEXT    DEFAULT ''",
        "analyzed_at":  "TEXT    DEFAULT (datetime('now'))",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init(self):
        columns_sql = ",\n                ".join(
            f"{col:<15} {ddl}" for col, ddl in self._FUNCTIONS_COLUMNS.items()
        )
        with self._conn() as conn:
            conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS functions (
                id              INTEGER PRIMARY KEY,
                {columns_sql}
            );

            CREATE TABLE IF NOT EXISTS recovered_types (
                id              INTEGER PRIMARY KEY,
                name            TEXT    NOT NULL UNIQUE,
                kind            TEXT    NOT NULL,   -- 'struct'|'class'|'enum'|'typedef'
                definition      TEXT    NOT NULL,
                source_function TEXT    DEFAULT '',  -- address of function that surfaced it
                confidence      REAL    DEFAULT 1.0
            );

            CREATE TABLE IF NOT EXISTS variable_names (
                id              INTEGER PRIMARY KEY,
                function_addr   TEXT    NOT NULL,
                original_name   TEXT    NOT NULL,
                inferred_name   TEXT    NOT NULL,
                inferred_type   TEXT    DEFAULT '',
                UNIQUE(function_addr, original_name)
            );

            CREATE TABLE IF NOT EXISTS call_graph (
                id              INTEGER PRIMARY KEY,
                caller_addr     TEXT    NOT NULL,
                callee_name     TEXT    NOT NULL,
                UNIQUE(caller_addr, callee_name)
            );

            CREATE TABLE IF NOT EXISTS known_apis (
                name        TEXT    PRIMARY KEY,
                signature   TEXT    NOT NULL,
                description TEXT    DEFAULT ''
            );
            """)
            self._migrate_functions_columns(conn)

    def _migrate_functions_columns(self, conn):
        """
        Retrofit any `_FUNCTIONS_COLUMNS` entries missing from an existing
        `functions` table — `CREATE TABLE IF NOT EXISTS` only helps brand-new
        databases; a database created before a schema change would otherwise
        crash on the first read/write of the new column (this has already
        happened once, with `pass6_output`).
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(functions)")}
        for col, ddl in self._FUNCTIONS_COLUMNS.items():
            if col in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE functions ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # e.g. a non-constant DEFAULT, which ADD COLUMN can't take

    # ------------------------------------------------------------------
    # Functions table
    # ------------------------------------------------------------------

    def upsert_function(self, address: str, name: str, signature: str = ""):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO functions (address, name, signature)
                VALUES (?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    name      = excluded.name,
                    signature = excluded.signature
            """, (address, name, signature))

    def set_pass_output(self, address: str, pass_num: int, output: str):
        col = f"pass{pass_num}_output"
        with self._conn() as conn:
            conn.execute(f"""
                UPDATE functions SET {col} = ? WHERE address = ?
            """, (output, address))

    def set_final_cpp(self, address: str, cpp: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET final_cpp = ? WHERE address = ?
            """, (cpp, address))

    def set_ai_name(self, address: str, name: str):
        with self._conn() as conn:
            conn.execute("UPDATE functions SET ai_name = ? WHERE address = ?", (name, address))

    def get_ai_name(self, address: str) -> str:
        fn = self.get_function(address)
        return (fn.get("ai_name") or "") if fn else ""

    def set_provider(self, address: str, provider: str):
        with self._conn() as conn:
            conn.execute("UPDATE functions SET provider = ? WHERE address = ?", (provider, address))

    def is_complete_for_provider(self, address: str, provider: str) -> bool:
        """
        True if this function already has a final result produced by this
        exact provider — used to skip re-translating on a resumed run.
        A stored result from a *different* provider doesn't count as done,
        since switching providers is a deliberate choice to get fresh output.
        """
        fn = self.get_function(address)
        return bool(fn and fn.get("final_cpp") and fn.get("provider") == provider)

    def set_summary(self, address: str, summary: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET summary = ? WHERE address = ?
            """, (summary, address))

    def get_function(self, address: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM functions WHERE address = ?
            """, (address,)).fetchone()
        return dict(row) if row else None

    def get_pass_output(self, address: str, pass_num: int) -> str:
        fn = self.get_function(address)
        if not fn:
            return ""
        return fn.get(f"pass{pass_num}_output", "") or ""

    def get_summary(self, address: str) -> str:
        fn = self.get_function(address)
        return fn.get("summary", "") if fn else ""

    # ------------------------------------------------------------------
    # Recovered types
    # ------------------------------------------------------------------

    def upsert_type(self, name: str, kind: str, definition: str,
                    source_function: str = "", confidence: float = 1.0):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO recovered_types (name, kind, definition, source_function, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    definition      = excluded.definition,
                    source_function = excluded.source_function,
                    confidence      = excluded.confidence
            """, (name, kind, definition, source_function, confidence))

    def get_all_types(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM recovered_types ORDER BY kind, name").fetchall()
        return [dict(r) for r in rows]

    def get_types_for_context(self, limit: int = 20) -> str:
        types = self.get_all_types()[:limit]
        if not types:
            return ""
        lines = []
        for t in types:
            lines.append(f"// {t['kind']}: {t['name']}")
            lines.append(t["definition"])
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Variable names
    # ------------------------------------------------------------------

    def upsert_variable(self, function_addr: str, original_name: str,
                        inferred_name: str, inferred_type: str = ""):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO variable_names (function_addr, original_name, inferred_name, inferred_type)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(function_addr, original_name) DO UPDATE SET
                    inferred_name = excluded.inferred_name,
                    inferred_type = excluded.inferred_type
            """, (function_addr, original_name, inferred_name, inferred_type))

    def get_variable_map(self, function_addr: str) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT original_name, inferred_name FROM variable_names
                WHERE function_addr = ?
            """, (function_addr,)).fetchall()
        return {r["original_name"]: r["inferred_name"] for r in rows}

    # ------------------------------------------------------------------
    # Known APIs
    # ------------------------------------------------------------------

    def seed_known_apis(self, apis: dict):
        """
        Populate known_apis from a {name: (signature, description)} dict.
        Uses INSERT OR IGNORE so re-seeding on every run is safe and fast.
        """
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO known_apis (name, signature, description) VALUES (?, ?, ?)",
                [(name, sig, desc) for name, (sig, desc) in apis.items()],
            )

    def get_api_context(self, import_names: list[str]) -> str:
        """
        Return a formatted block of known signatures for the given import names.
        Used to inject ground-truth API context into prompts.
        """
        if not import_names:
            return ""
        placeholders = ",".join("?" * len(import_names))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT name, signature, description FROM known_apis WHERE name IN ({placeholders})",
                import_names,
            ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            lines.append(f"  {r['signature']}")
            if r["description"]:
                lines.append(f"    // {r['description']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Call graph
    # ------------------------------------------------------------------

    def add_call_edge(self, caller_addr: str, callee_name: str):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO call_graph (caller_addr, callee_name)
                VALUES (?, ?)
            """, (caller_addr, callee_name))

    def get_callee_summaries(self, caller_addr: str) -> list[dict]:
        """Return summaries for all functions directly called by caller_addr."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT f.name, f.signature, f.summary
                FROM call_graph cg
                JOIN functions f ON f.name = cg.callee_name
                WHERE cg.caller_addr = ?
                  AND f.summary != ''
            """, (caller_addr,)).fetchall()
        return [dict(r) for r in rows]

    def get_caller_summaries(self, function_name: str) -> list[dict]:
        """Return summaries for all functions that call function_name."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT f.name, f.signature, f.summary
                FROM call_graph cg
                JOIN functions f ON f.address = cg.caller_addr
                WHERE cg.callee_name = ?
                  AND f.summary != ''
            """, (function_name,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
