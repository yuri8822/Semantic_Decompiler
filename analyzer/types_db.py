"""
Semantic memory database.

Stores everything the pipeline learns across functions so later functions
and later runs can build on prior discoveries rather than starting blind.

Schema overview
---------------
functions        — per-function analysis state (one translation attempt per row)
recovered_types  — inferred structs / classes / enums / typedefs
variable_names   — inferred names for compiler-generated variable IDs
call_graph       — caller→callee edges (populated from Ghidra export, joined
                   by callee *name* — kept as-is for the existing prompt
                   context queries; see `relationships` below for the
                   address-qualified version)
known_apis       — curated CRT/Win32 (and, later, library-tagged) signatures

entities         — whole-program knowledge graph nodes: functions, classes,
                   globals, types, enums, variables. One row per
                   (binary, kind, key).
entity_facts     — append-only fact log per entity (name, type, field, ...).
                   Exactly one row per (entity_id, fact_type) has
                   is_current=1 at a time; older rows are kept as history,
                   never deleted, so no fact is ever silently lost.
relationships    — append-only edges between entities (calls, references,
                   inherits_from, member_of, ...), same current/history
                   shape as entity_facts. Unlike call_graph, edges point at
                   a real entity id (resolved via callee *address*), so two
                   same-named functions in one binary can't be confused.
contradictions   — logged whenever a new fact materially disagrees with the
                   current one for (entity_id, fact_type), whether or not
                   the new fact actually won.
"""

import json
import sqlite3
from contextlib import contextmanager
from typing import Optional

# Confidence formula (architecture-mission Phase 5): confidence =
# base_weight(source_type) + evidence_bonus. Deterministic (Ghidra-derived:
# signatures, CFG analysis, p-code) sources are treated as near-ground-truth;
# AI-proposed facts start lower and only gain confidence from genuinely
# distinct corroborating evidence.
#
# Deliberately uncapped (no artificial 1.0 ceiling): the plan's own design
# goal is that "a well-corroborated AI correction must be able to eventually
# beat a wrong deterministic fact" (cfg_builder's own analyses are
# approximations, not ground truth either). Verified this is actually
# achievable with these constants: a maximally-evidenced AI fact
# (0.5 + 3*0.2 = 1.1) exceeds even a zero-evidence deterministic fact's
# override threshold (0.9 + MARGIN = 1.0) — capping at a flat 1.0 (an
# earlier version of this formula did, with a 0.15 step) would have made
# that structurally impossible instead, silently contradicting the design
# goal. The min(distinct, 3) cap already bounds the natural maximum (1.5
# for deterministic, 1.1 for AI), so no separate ceiling is needed.
_CONFIDENCE_BASE_WEIGHT = {
    "deterministic": 0.9,
    "ai": 0.5,
}
_CONFIDENCE_EVIDENCE_STEP = 0.2
_CONFIDENCE_EVIDENCE_CAP = 3

# A new fact must clear the current one's confidence by more than this
# margin to actually become `is_current` — without a margin, near-tied
# conclusions would flip back and forth on every re-evaluation.
CONFIDENCE_MARGIN = 0.1


def compute_confidence(source_type: str, evidence: Optional[list] = None) -> float:
    """
    confidence = base_weight(source_type) + 0.2 * min(distinct evidence
    categories passed in THIS call, 3).

    Deliberately counts DISTINCT evidence categories from a single call,
    never distinct passes or repeated restatements: the translator already
    threads `current_code` pass-to-pass, so the same model repeating its
    own earlier conclusion across passes is not independent evidence — and
    counting occurrences instead of distinct categories would also let
    Bonsai's documented repetition-loop failure mode inflate confidence for
    free. `source_type` defaults to the lower ("ai") weight for anything
    unrecognized, so a typo'd source_type fails safe rather than fails open.
    """
    base = _CONFIDENCE_BASE_WEIGHT.get(source_type, _CONFIDENCE_BASE_WEIGHT["ai"])
    distinct = len(set(evidence or []))
    bonus = _CONFIDENCE_EVIDENCE_STEP * min(distinct, _CONFIDENCE_EVIDENCE_CAP)
    return round(base + bonus, 4)


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
        "ai_name":      "TEXT    DEFAULT ''",  # name chosen by the AI for this function
        "provider":     "TEXT    DEFAULT ''",  # which LLM provider produced the current results
        "summary":      "TEXT    DEFAULT ''",  # one-line AI-generated description
        "final_cpp":    "TEXT    DEFAULT ''",  # the single translation pass's accepted output
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

            CREATE TABLE IF NOT EXISTS entities (
                id      INTEGER PRIMARY KEY,
                binary  TEXT    NOT NULL,
                kind    TEXT    NOT NULL,  -- 'function'|'class'|'global'|'type'|'enum'|'variable'
                key     TEXT    NOT NULL,  -- address for function; name for class/type/global;
                                           -- "addr:orig_name" for variable
                UNIQUE(binary, kind, key)
            );

            CREATE TABLE IF NOT EXISTS entity_facts (
                id          INTEGER PRIMARY KEY,
                entity_id   INTEGER NOT NULL REFERENCES entities(id),
                fact_type   TEXT    NOT NULL,
                value       TEXT    NOT NULL,
                confidence  REAL    NOT NULL,
                evidence    TEXT    NOT NULL DEFAULT '[]',  -- JSON array
                source_pass INTEGER,
                provider    TEXT    DEFAULT '',
                is_current  INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_facts_current
                ON entity_facts(entity_id, fact_type) WHERE is_current = 1;
            CREATE INDEX IF NOT EXISTS ix_facts_entity
                ON entity_facts(entity_id, fact_type, is_current);

            CREATE TABLE IF NOT EXISTS relationships (
                id             INTEGER PRIMARY KEY,
                binary         TEXT    NOT NULL,
                src_entity_id  INTEGER NOT NULL REFERENCES entities(id),
                dst_entity_id  INTEGER NOT NULL REFERENCES entities(id),
                rel_type       TEXT    NOT NULL,
                confidence     REAL    NOT NULL,
                evidence       TEXT    NOT NULL DEFAULT '[]',
                is_current     INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT    DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_rel_current
                ON relationships(src_entity_id, dst_entity_id, rel_type) WHERE is_current = 1;
            CREATE INDEX IF NOT EXISTS ix_rel_dst
                ON relationships(dst_entity_id, rel_type, is_current);

            CREATE TABLE IF NOT EXISTS contradictions (
                id          INTEGER PRIMARY KEY,
                entity_id   INTEGER NOT NULL REFERENCES entities(id),
                fact_type   TEXT    NOT NULL,
                old_fact_id INTEGER,
                new_fact_id INTEGER NOT NULL,
                resolved    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
            """)
            self._migrate_functions_columns(conn)
            self._migrate_call_graph(conn)
            self._migrate_known_apis(conn)

    def _migrate_functions_columns(self, conn):
        """
        Retrofit any `_FUNCTIONS_COLUMNS` entries missing from an existing
        `functions` table (adds) and drop any columns the live table has
        that are no longer declared (removes) — `CREATE TABLE IF NOT
        EXISTS` only helps brand-new databases; a database created before a
        schema change would otherwise crash on the first read/write of a
        new column (this has already happened twice: `pass6_output`, then
        `provider`), or just carry dead columns forever (the single-pass
        rewrite dropped `pass1_output`..`pass6_output` and
        `refinement_round` when the multi-pass pipeline was removed).

        Tries the cheap path first for both directions: `ALTER TABLE ADD
        COLUMN` for additions, `ALTER TABLE DROP COLUMN` for removals
        (available since SQLite 3.35; this project's environment runs
        3.42+). SQLite refuses ADD COLUMN for anything with a PRIMARY
        KEY/UNIQUE/CHECK constraint, a NOT NULL without a constant default,
        or a non-constant DEFAULT (e.g. `datetime('now')`); DROP COLUMN is
        refused if the column is part of a UNIQUE/PRIMARY KEY constraint,
        an index, or a generated-column expression. Either failure falls
        back to a full table rebuild.

        A database missing `binary` predates per-binary address scoping —
        its UNIQUE constraint is on `address` alone, which ALTER TABLE
        cannot change to `(binary, address)` no matter what. That always
        needs the full rebuild, regardless of whether `binary` itself could
        be added cheaply.
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(functions)")}
        missing = [col for col in self._FUNCTIONS_COLUMNS if col not in existing]
        obsolete = [col for col in existing if col not in self._FUNCTIONS_COLUMNS and col != "id"]
        if not missing and not obsolete:
            return

        force_rebuild = "binary" in missing

        needs_rebuild = []
        if not force_rebuild:
            for col in missing:
                try:
                    conn.execute(f"ALTER TABLE functions ADD COLUMN {col} {self._FUNCTIONS_COLUMNS[col]}")
                except sqlite3.OperationalError:
                    needs_rebuild.append(col)
            for col in obsolete:
                try:
                    conn.execute(f"ALTER TABLE functions DROP COLUMN {col}")
                except sqlite3.OperationalError:
                    needs_rebuild.append(col)

        if force_rebuild or needs_rebuild:
            reason = ("missing per-binary address scoping" if force_rebuild
                      else f"add/drop: {', '.join(missing + obsolete)}")
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

    def _migrate_known_apis(self, conn):
        """`library` is a brand-new, nullable, no-constraint column — the
        cheap ALTER path always works for this one, no rebuild needed."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(known_apis)")}
        if "library" in existing:
            return
        conn.execute("ALTER TABLE known_apis ADD COLUMN library TEXT DEFAULT ''")

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

    def clear_result(self, binary: str, address: str):
        """
        Wipe final_cpp, ai_name, and summary for a function. Used when
        switching providers, so a stale result from a different provider
        can never be left dangling under the new provider's marker —
        otherwise an interruption right after the switch (before the new
        provider has produced anything) would let old data masquerade as
        "complete" under the new provider on a later resumed run.
        """
        with self._conn() as conn:
            conn.execute("""
                UPDATE functions SET final_cpp = '', ai_name = '', summary = ''
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

    def get_summary(self, binary: str, address: str) -> str:
        fn = self.get_function(binary, address)
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

    def tag_known_api_libraries(self, tags: dict):
        """Bulk-update known_apis.library from a {name: library} mapping (Phase 2)."""
        with self._conn() as conn:
            conn.executemany(
                "UPDATE known_apis SET library = ? WHERE name = ?",
                [(library, name) for name, library in tags.items()],
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
    # Knowledge graph: entities / facts / relationships / contradictions
    # ------------------------------------------------------------------
    #
    # Schema from Phase 1 (pure additive side-data), evidence generation
    # from Phase 2, prompt consumption from Phase 3. Phase 5 adds the
    # confidence formula, the margin-gated overwrite rule, and
    # contradiction logging in `_insert_fact` below.

    def create_entity(self, binary: str, kind: str, key: str) -> int:
        """Idempotently create (or fetch) an entity, returning its id."""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO entities (binary, kind, key) VALUES (?, ?, ?)
            """, (binary, kind, key))
            row = conn.execute("""
                SELECT id FROM entities WHERE binary = ? AND kind = ? AND key = ?
            """, (binary, kind, key)).fetchone()
        return row["id"]

    def get_entity_id(self, binary: str, kind: str, key: str) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT id FROM entities WHERE binary = ? AND kind = ? AND key = ?
            """, (binary, kind, key)).fetchone()
        return row["id"] if row else None

    def get_entity(self, entity_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return dict(row) if row else None

    def get_entities_by_kind(self, binary: str, kind: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM entities WHERE binary = ? AND kind = ?
            """, (binary, kind)).fetchall()
        return [dict(r) for r in rows]

    def record_fact(self, entity_id: int, fact_type: str, value: str,
                     confidence: float, evidence: Optional[list] = None,
                     source_pass: Optional[int] = None, provider: str = "") -> int:
        """
        Append a new fact for (entity_id, fact_type), marking it current and
        superseding whatever was current before (the old row is kept, not
        deleted — it's still readable via get_entity_facts(current_only=False)).
        """
        with self._conn() as conn:
            return self._insert_fact(conn, entity_id, fact_type, value,
                                      confidence, evidence, source_pass, provider)

    def record_facts_batch(self, facts: list[dict]) -> list[int]:
        """
        Record multiple facts in one connection/transaction instead of one
        per fact — a single pass can easily emit 5-20 facts (one per field,
        one per relationship), and record_fact's per-call connection would
        make that N micro-transactions instead of one.
        Each dict: entity_id, fact_type, value, confidence, and optionally
        evidence (list), source_pass, provider.
        """
        ids = []
        with self._conn() as conn:
            for f in facts:
                ids.append(self._insert_fact(
                    conn, f["entity_id"], f["fact_type"], f["value"], f["confidence"],
                    f.get("evidence"), f.get("source_pass"), f.get("provider", ""),
                ))
        return ids

    def _insert_fact(self, conn, entity_id: int, fact_type: str, value: str,
                      confidence: float, evidence: Optional[list],
                      source_pass: Optional[int], provider: str) -> int:
        """
        Append the new fact row (always — history is never lost, even when
        it loses below). If no fact of this type exists yet for the entity,
        the new row becomes current immediately (nothing to compare
        against). Otherwise: a materially different value ALWAYS logs a
        `contradictions` row, regardless of whether the margin below is
        cleared; the new row only actually becomes `is_current` if
        `new.confidence > old.confidence + CONFIDENCE_MARGIN` — applied
        symmetrically regardless of source, so a well-corroborated
        correction can eventually beat a previously-confident but wrong
        fact from any source.
        """
        existing = conn.execute("""
            SELECT * FROM entity_facts WHERE entity_id = ? AND fact_type = ? AND is_current = 1
        """, (entity_id, fact_type)).fetchone()

        cur = conn.execute("""
            INSERT INTO entity_facts
                (entity_id, fact_type, value, confidence, evidence, source_pass, provider, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (entity_id, fact_type, value, confidence, json.dumps(evidence or []),
              source_pass, provider))
        new_id = cur.lastrowid

        if existing is None:
            conn.execute("UPDATE entity_facts SET is_current = 1 WHERE id = ?", (new_id,))
            return new_id

        if existing["value"] != value:
            conn.execute("""
                INSERT INTO contradictions (entity_id, fact_type, old_fact_id, new_fact_id)
                VALUES (?, ?, ?, ?)
            """, (entity_id, fact_type, existing["id"], new_id))

        if confidence > existing["confidence"] + CONFIDENCE_MARGIN:
            conn.execute("""
                UPDATE entity_facts SET is_current = 0
                WHERE entity_id = ? AND fact_type = ? AND is_current = 1
            """, (entity_id, fact_type))
            conn.execute("UPDATE entity_facts SET is_current = 1 WHERE id = ?", (new_id,))
        # else: stays is_current=0 — outranked, but preserved in history.

        return new_id

    def get_current_fact(self, entity_id: int, fact_type: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM entity_facts
                WHERE entity_id = ? AND fact_type = ? AND is_current = 1
            """, (entity_id, fact_type)).fetchone()
        return dict(row) if row else None

    def get_entity_facts(self, entity_id: int, current_only: bool = True) -> list[dict]:
        q = "SELECT * FROM entity_facts WHERE entity_id = ?"
        if current_only:
            q += " AND is_current = 1"
        with self._conn() as conn:
            rows = conn.execute(q, (entity_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_relationship(self, binary: str, src_entity_id: int, dst_entity_id: int,
                          rel_type: str, confidence: float = 0.9,
                          evidence: Optional[list] = None) -> int:
        with self._conn() as conn:
            conn.execute("""
                UPDATE relationships SET is_current = 0
                WHERE src_entity_id = ? AND dst_entity_id = ? AND rel_type = ? AND is_current = 1
            """, (src_entity_id, dst_entity_id, rel_type))
            cur = conn.execute("""
                INSERT INTO relationships
                    (binary, src_entity_id, dst_entity_id, rel_type, confidence, evidence, is_current)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (binary, src_entity_id, dst_entity_id, rel_type, confidence,
                  json.dumps(evidence or [])))
        return cur.lastrowid

    def get_relationships(self, entity_id: int, direction: str = "out",
                           rel_type: Optional[str] = None) -> list[dict]:
        """direction: 'out' (entity_id is the source) or 'in' (entity_id is the destination)."""
        col = "src_entity_id" if direction == "out" else "dst_entity_id"
        q = f"SELECT * FROM relationships WHERE {col} = ? AND is_current = 1"
        params: list = [entity_id]
        if rel_type:
            q += " AND rel_type = ?"
            params.append(rel_type)
        with self._conn() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def record_contradiction(self, entity_id: int, fact_type: str,
                              old_fact_id: Optional[int], new_fact_id: int) -> int:
        """
        Logged whenever a new fact materially disagrees with the current
        one for (entity_id, fact_type) — regardless of whether the new fact
        actually won the overwrite (that decision belongs to whatever calls
        this, not to record_fact itself).
        """
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO contradictions (entity_id, fact_type, old_fact_id, new_fact_id)
                VALUES (?, ?, ?, ?)
            """, (entity_id, fact_type, old_fact_id, new_fact_id))
        return cur.lastrowid

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
