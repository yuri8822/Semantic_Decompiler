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
    #
    # `address` alone is NOT globally unique: two different binaries can
    # (and, for native PE executables at the default load address, commonly
    # do) share the same address for an unrelated function. Without `binary`
    # scoping this, analyzing a second binary could silently splice a first
    # binary's cached translation into the second's output wherever their
    # address spaces overlap — confirmed happening in practice with
    # find.exe and Chess.exe both having a function at 0x140001010.
    _FUNCTIONS_COLUMNS = {
        "binary":       "TEXT    NOT NULL DEFAULT ''",  # which binary this row belongs to
        "name":         "TEXT    NOT NULL",
        "address":      "TEXT    NOT NULL",
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

    @classmethod
    def _functions_columns_sql(cls, indent: str = "                ") -> str:
        return (",\n" + indent).join(
            f"{col:<15} {ddl}" for col, ddl in cls._FUNCTIONS_COLUMNS.items()
        )

    def init(self):
        with self._conn() as conn:
            conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS functions (
                id              INTEGER PRIMARY KEY,
                {self._functions_columns_sql()},
                UNIQUE(binary, address)
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
                binary          TEXT    NOT NULL DEFAULT '',
                caller_addr     TEXT    NOT NULL,
                callee_name     TEXT    NOT NULL,
                UNIQUE(binary, caller_addr, callee_name)
            );

            CREATE TABLE IF NOT EXISTS known_apis (
                name        TEXT    PRIMARY KEY,
                signature   TEXT    NOT NULL,
                description TEXT    DEFAULT ''
            );
            """)
            self._migrate_functions_columns(conn)
            self._migrate_call_graph(conn)

    def _migrate_functions_columns(self, conn):
        """
        Retrofit any `_FUNCTIONS_COLUMNS` entries missing from an existing
        `functions` table — `CREATE TABLE IF NOT EXISTS` only helps brand-new
        databases; a database created before a schema change would otherwise
        crash on the first read/write of the new column (this has already
        happened twice: `pass6_output`, then `provider`).

        Tries the cheap path first (`ALTER TABLE ADD COLUMN`), which covers
        ordinary additive columns. SQLite refuses that for anything with a
        PRIMARY KEY/UNIQUE/CHECK constraint, a NOT NULL without a constant
        default, or a non-constant DEFAULT (e.g. `datetime('now')`) — for
        those, falls back to a full table rebuild.

        A database missing `binary` predates per-binary address scoping —
        its UNIQUE constraint is on `address` alone, which ALTER TABLE
        cannot change to `(binary, address)` no matter what. That always
        needs the full rebuild, regardless of whether `binary` itself could
        be added cheaply.
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(functions)")}
        missing = [col for col in self._FUNCTIONS_COLUMNS if col not in existing]
        if not missing:
            return

        force_rebuild = "binary" in missing

        needs_rebuild = []
        if not force_rebuild:
            for col in missing:
                try:
                    conn.execute(f"ALTER TABLE functions ADD COLUMN {col} {self._FUNCTIONS_COLUMNS[col]}")
                except sqlite3.OperationalError:
                    needs_rebuild.append(col)

        if force_rebuild or needs_rebuild:
            reason = ("missing per-binary address scoping" if force_rebuild
                      else f"add: {', '.join(needs_rebuild)}")
            print(f"[types_db] Rebuilding functions table ({reason})")
            self._rebuild_functions_table(conn)

    def _rebuild_functions_table(self, conn):
        """
        Recreate `functions` with the current `_FUNCTIONS_COLUMNS` schema
        (and its `UNIQUE(binary, address)` constraint) and copy over every
        column that already exists (columns brand-new to this rebuild get
        their schema default, same as a fresh row would — e.g. rows from
        before `binary` existed all get `binary=''`). SQLite DDL is
        transactional, so this rolls back cleanly with the rest of
        `init()`'s `_conn()` block if anything here fails.
        """
        existing_cols = [row["name"] for row in conn.execute("PRAGMA table_info(functions)")]
        carried_over = [col for col in self._FUNCTIONS_COLUMNS if col in existing_cols]
        col_list = ", ".join(carried_over)

        conn.execute(f"""
            CREATE TABLE functions_rebuild (
                id              INTEGER PRIMARY KEY,
                {self._functions_columns_sql(indent="                ")},
                UNIQUE(binary, address)
            )
        """)
        conn.execute(f"""
            INSERT INTO functions_rebuild (id, {col_list})
            SELECT id, {col_list} FROM functions
        """)
        conn.execute("DROP TABLE functions")
        conn.execute("ALTER TABLE functions_rebuild RENAME TO functions")

    def _migrate_call_graph(self, conn):
        """
        Same fix as `_migrate_functions_columns`, scoped to `call_graph`: a
        table missing `binary` predates per-binary scoping, and its old
        `UNIQUE(caller_addr, callee_name)` constraint can't distinguish two
        different binaries' call edges — the same category of bug as the
        `functions.address` collision, just one step removed (it corrupts
        cross-function *context* fed into prompts, not a function's own
        stored translation). `ALTER TABLE` can't change an existing UNIQUE
        constraint, so this always needs a rebuild rather than a cheap ADD
        COLUMN.
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(call_graph)")}
        if "binary" in existing:
            return

        print("[types_db] Rebuilding call_graph table (missing per-binary scoping)")
        conn.execute("""
            CREATE TABLE call_graph_rebuild (
                id              INTEGER PRIMARY KEY,
                binary          TEXT    NOT NULL DEFAULT '',
                caller_addr     TEXT    NOT NULL,
                callee_name     TEXT    NOT NULL,
                UNIQUE(binary, caller_addr, callee_name)
            )
        """)
        conn.execute("""
            INSERT INTO call_graph_rebuild (id, caller_addr, callee_name)
            SELECT id, caller_addr, callee_name FROM call_graph
        """)
        conn.execute("DROP TABLE call_graph")
        conn.execute("ALTER TABLE call_graph_rebuild RENAME TO call_graph")

    # ------------------------------------------------------------------
    # Functions table
    # ------------------------------------------------------------------

    def upsert_function(self, binary: str, address: str, name: str, signature: str = ""):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO functions (binary, address, name, signature)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(binary, address) DO UPDATE SET
                    name      = excluded.name,
                    signature = excluded.signature
            """, (binary, address, name, signature))

    def set_pass_output(self, binary: str, address: str, pass_num: int, output: str):
        col = f"pass{pass_num}_output"
        with self._conn() as conn:
            conn.execute(f"""
                UPDATE functions SET {col} = ? WHERE binary = ? AND address = ?
            """, (output, binary, address))

    def set_final_cpp(self, binary: str, address: str, cpp: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET final_cpp = ? WHERE binary = ? AND address = ?
            """, (cpp, binary, address))

    def set_ai_name(self, binary: str, address: str, name: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET ai_name = ? WHERE binary = ? AND address = ?
            """, (name, binary, address))

    def get_ai_name(self, binary: str, address: str) -> str:
        fn = self.get_function(binary, address)
        return (fn.get("ai_name") or "") if fn else ""

    def set_provider(self, binary: str, address: str, provider: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET provider = ? WHERE binary = ? AND address = ?
            """, (provider, binary, address))

    def clear_pass_data(self, binary: str, address: str):
        """
        Wipe all pass outputs, final_cpp, ai_name, and summary for a
        function. Used when switching providers, so stale results from a
        different provider can never be left dangling under the new
        provider's marker — otherwise an interruption right after the
        switch (before the new provider has produced anything) would let
        old, unmigrated data masquerade as "complete" under the new
        provider on a later resumed run.
        """
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET
                    pass1_output = '', pass2_output = '', pass3_output = '',
                    pass4_output = '', pass5_output = '', pass6_output = '',
                    final_cpp = '', ai_name = '', summary = ''
                WHERE binary = ? AND address = ?
            """, (binary, address))

    def is_complete_for_provider(self, binary: str, address: str, provider: str) -> bool:
        """
        True if this function already has a final result produced by this
        exact provider — used to skip re-translating on a resumed run.
        A stored result from a *different* provider doesn't count as done,
        since switching providers is a deliberate choice to get fresh output.
        """
        fn = self.get_function(binary, address)
        return bool(fn and fn.get("final_cpp") and fn.get("provider") == provider)

    def set_summary(self, binary: str, address: str, summary: str):
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET summary = ? WHERE binary = ? AND address = ?
            """, (summary, binary, address))

    def get_function(self, binary: str, address: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM functions WHERE binary = ? AND address = ?
            """, (binary, address)).fetchone()
        return dict(row) if row else None

    def get_pass_output(self, binary: str, address: str, pass_num: int) -> str:
        fn = self.get_function(binary, address)
        if not fn:
            return ""
        return fn.get(f"pass{pass_num}_output", "") or ""

    def get_summary(self, binary: str, address: str) -> str:
        fn = self.get_function(binary, address)
        return fn.get("summary", "") if fn else ""

    def get_all_functions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM functions ORDER BY binary, address").fetchall()
        return [dict(r) for r in rows]

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

    def get_all_variable_names(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM variable_names ORDER BY function_addr, original_name
            """).fetchall()
        return [dict(r) for r in rows]

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
    #
    # Binary-scoped like `functions`, which closes the cross-binary version
    # of this bug (two different binaries sharing a callee name). One
    # narrower case remains open: the join is still by function *name*
    # within a binary, and Ghidra only gives us callee names, not callee
    # addresses — so two functions with the same name *within one binary*
    # (this happens: Chess.exe itself has two functions both named
    # `__do_global_ctors`, at different addresses) still can't be told
    # apart. Fixing that would mean capturing callee addresses in
    # ghidra_scripts/ExportAnalysis.java's export and threading that through
    # parse_output.py and the callee-guard logic in ai/translator.py too —
    # a materially bigger change than this fix, left for a future pass.

    def get_all_call_graph(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM call_graph ORDER BY binary, caller_addr, callee_name
            """).fetchall()
        return [dict(r) for r in rows]

    def add_call_edge(self, binary: str, caller_addr: str, callee_name: str):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO call_graph (binary, caller_addr, callee_name)
                VALUES (?, ?, ?)
            """, (binary, caller_addr, callee_name))

    def get_callee_summaries(self, binary: str, caller_addr: str) -> list[dict]:
        """Return summaries for all functions (in the same binary) directly called by caller_addr."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT f.name, f.signature, f.summary
                FROM call_graph cg
                JOIN functions f ON f.name = cg.callee_name AND f.binary = cg.binary
                WHERE cg.binary = ? AND cg.caller_addr = ?
                  AND f.summary != ''
            """, (binary, caller_addr)).fetchall()
        return [dict(r) for r in rows]

    def get_caller_summaries(self, binary: str, function_name: str) -> list[dict]:
        """Return summaries for all functions (in the same binary) that call function_name."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT f.name, f.signature, f.summary
                FROM call_graph cg
                JOIN functions f ON f.address = cg.caller_addr AND f.binary = cg.binary
                WHERE cg.binary = ? AND cg.callee_name = ?
                  AND f.summary != ''
            """, (binary, function_name)).fetchall()
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
