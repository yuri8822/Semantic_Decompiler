"""
Builds a networkx DiGraph from the CFG exported by Ghidra and produces
a concise text summary for injection into the AI prompt.

Summary covers:
  - block / edge count
  - entry point
  - detected loops (back-edges via simple_cycles)
  - number of conditional branches
  - unreachable blocks (dead code)

This module also computes pure deterministic evidence (architecture-mission
Phase 2) for the knowledge graph: real dominator-based natural loops
(replacing simple_cycles' elementary-cycle counting for anything
evidence-grade), a bounded intraprocedural constant-propagation pass over
the function's own SSA p-code, calling-convention extraction from Ghidra's
own signature string, and partial pointer-aliasing hints. All of it is pure
static analysis — no AI, no path/branch reasoning, no cross-function
propagation, no symbolic execution. `analyze_deterministic()` is the single
entry point the pipeline calls; it's deliberately DB-agnostic (returns plain
fact dicts, same as `build_cfg_summary`'s existing text-only contract) —
the caller attaches entity_id/confidence and hands them to
`SemanticDB.record_facts_batch()`.
"""

import json
import re

import networkx as nx


def build_cfg_summary(function_data) -> str:
    """
    Accept a FunctionData object or plain dict.
    Returns a human-readable CFG summary string (or empty string if no CFG data).
    """
    # Accept both pydantic model and dict
    if hasattr(function_data, "cfg"):
        cfg = function_data.cfg
        blocks = cfg.blocks
        edges  = cfg.edges
        fn_name = function_data.name
    else:
        cfg_dict = function_data.get("cfg", {})
        blocks   = cfg_dict.get("blocks", [])
        edges    = cfg_dict.get("edges", [])
        fn_name  = function_data.get("name", "?")

    if not blocks:
        return ""

    G = nx.DiGraph()

    for b in blocks:
        addr = b.address if hasattr(b, "address") else b.get("address", "?")
        G.add_node(addr)

    for e in edges:
        if hasattr(e, "from_addr"):
            src, dst, ftype = e.from_addr, e.to_addr, e.flow_type
        else:
            src, dst, ftype = e.get("from_addr", "?"), e.get("to_addr", "?"), e.get("flow_type", "")
        G.add_edge(src, dst, flow_type=ftype)

    entry = (blocks[0].address if hasattr(blocks[0], "address") else blocks[0].get("address")) \
            if blocks else None

    lines = [f"CFG summary for {fn_name}:"]
    lines.append(f"  Blocks : {G.number_of_nodes()}")
    lines.append(f"  Edges  : {G.number_of_edges()}")

    if entry:
        lines.append(f"  Entry  : {entry}")

    # Loops — detect via simple_cycles (reliable, handles nested loops)
    try:
        cycles = list(nx.simple_cycles(G))
        if cycles:
            lines.append(f"  Loops  : {len(cycles)} cycle(s) detected")
            for i, cyc in enumerate(cycles[:3]):
                lines.append(f"    loop {i+1}: {' → '.join(cyc[:4])}{'…' if len(cyc) > 4 else ''}")
        else:
            lines.append("  Loops  : none (acyclic)")
    except Exception:
        pass

    # Conditional branches (out-degree >= 2)
    branches = [n for n in G.nodes if G.out_degree(n) >= 2]
    if branches:
        lines.append(f"  Cond. branches: {len(branches)}")

    # Unreachable blocks
    if entry and G.has_node(entry):
        reachable   = nx.descendants(G, entry) | {entry}
        unreachable = set(G.nodes) - reachable
        if unreachable:
            lines.append(f"  Unreachable blocks: {len(unreachable)} (dead code)")

    # Estimate cyclomatic complexity: E - N + 2
    cc = G.number_of_edges() - G.number_of_nodes() + 2
    lines.append(f"  Cyclomatic complexity: {max(1, cc)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic evidence (architecture-mission Phase 2) — pure static
# analysis facts for the knowledge graph. Nothing below this point is
# consumed by build_cfg_summary() or any existing prompt path.
# ---------------------------------------------------------------------------

def _cfg_graph_and_entry(function_data):
    """Shared graph-building step for the dominator/loop analysis below."""
    if hasattr(function_data, "cfg"):
        cfg = function_data.cfg
        blocks, edges = cfg.blocks, cfg.edges
    else:
        cfg_dict = function_data.get("cfg", {})
        blocks, edges = cfg_dict.get("blocks", []), cfg_dict.get("edges", [])

    if not blocks:
        return None, None

    G = nx.DiGraph()
    for b in blocks:
        addr = b.address if hasattr(b, "address") else b.get("address", "?")
        G.add_node(addr)
    for e in edges:
        if hasattr(e, "from_addr"):
            src, dst = e.from_addr, e.to_addr
        else:
            src, dst = e.get("from_addr", "?"), e.get("to_addr", "?")
        G.add_edge(src, dst)

    entry = blocks[0].address if hasattr(blocks[0], "address") else blocks[0].get("address")
    return G, entry


def compute_natural_loops(function_data) -> list[dict]:
    """
    Real natural-loop detection via dominator analysis, in place of
    build_cfg_summary's nx.simple_cycles (elementary-cycle counting, which
    reports *a* cycle count but not loop structure). A back edge n->h exists
    when h dominates n; the natural loop for that back edge is every node
    that can reach n without passing through h — the standard
    back-edge/natural-loop construction, not symbolic/path execution.
    """
    G, entry = _cfg_graph_and_entry(function_data)
    if G is None or not entry or not G.has_node(entry):
        return []

    try:
        idom = nx.immediate_dominators(G, entry)
    except Exception:
        return []

    def dominators_of(n):
        seen = {n}
        cur = n
        while idom.get(cur) is not None and idom[cur] != cur:
            cur = idom[cur]
            if cur in seen:
                break
            seen.add(cur)
        return seen

    loops = []
    for u, v in G.edges():
        if v in dominators_of(u):  # back edge: u -> v where v dominates u
            body = {v, u}
            stack = [u]
            while stack:
                n = stack.pop()
                for pred in G.predecessors(n):
                    if pred == v or pred in body:
                        continue
                    body.add(pred)
                    stack.append(pred)
            loops.append({"header": v, "back_edge_from": u, "body_size": len(body)})

    return loops


_CALLING_CONV_RE = re.compile(r'__(cdecl|stdcall|thiscall|fastcall|vectorcall)\b')


def extract_calling_convention(function_data) -> str:
    """Deterministic extraction from Ghidra's own signature string — not a guess."""
    sig = function_data.signature if hasattr(function_data, "signature") else function_data.get("signature", "")
    m = _CALLING_CONV_RE.search(sig or "")
    return m.group(0) if m else ""


# Varnode token shape: (space,offset,size) — Ghidra's real PcodeOpAST.toString()
# output puts a space after each comma (e.g. "(register, 0x0, 8)"), confirmed
# against a real export (100% of 1939 real ops matched with \s*, 0 matched
# without it) — so, unlike ir_builder.py's same-shaped _VARNODE_RE (which
# lacks \s* and therefore silently fails to match real p-code text at all;
# flagged separately as a likely pre-existing bug there, out of this
# module's scope to fix), this copy tolerates the whitespace.
_VARNODE_RE = re.compile(r'\((\w+),\s*(0x[0-9a-fA-F]+|\d+),\s*(\d+)\)')

_CONST_FOLD = {
    "INT_ADD": lambda a, b: a + b, "INT_SUB": lambda a, b: a - b,
    "INT_MULT": lambda a, b: a * b, "INT_AND": lambda a, b: a & b,
    "INT_OR": lambda a, b: a | b, "INT_XOR": lambda a, b: a ^ b,
    "INT_LEFT": lambda a, b: a << b, "INT_RIGHT": lambda a, b: a >> b,
}


def _split_pcode_op(raw: str):
    """Minimal p-code op split: (mnemonic, output_varnode_str_or_None, [input_varnode_strs])."""
    raw = raw.strip()
    m = _VARNODE_RE.match(raw)
    output = None
    rest = raw
    if m:
        output = raw[m.start():m.end()]
        rest = raw[m.end():].strip()
    elif raw.startswith("---"):
        rest = raw[3:].strip()
    if rest.startswith("="):
        rest = rest[1:].strip()
    parts = rest.split(None, 1)
    if not parts:
        return "UNKNOWN", output, []
    mnemonic = parts[0]
    inputs = []
    if len(parts) > 1:
        # Find each complete (space,offset,size) varnode directly instead of
        # splitting on every comma first — a varnode's own internal commas
        # (e.g. "(ram, 0x140006668, 8)") are otherwise indistinguishable
        # from the top-level " , " separator between multiple inputs, and
        # a naive split shreds every multi-field varnode into unmatchable
        # fragments (confirmed against real multi-input ops like MULTIEQUAL).
        inputs = [mm.group(0) for mm in _VARNODE_RE.finditer(parts[1])]
    return mnemonic, output, inputs


def _const_value(varnode_str: str, known: dict):
    m = _VARNODE_RE.match(varnode_str.strip())
    if not m:
        return None
    space, offset = m.group(1), m.group(2)
    if space == "const":
        try:
            return int(offset, 16) if offset.startswith("0x") else int(offset)
        except ValueError:
            return None
    return known.get(varnode_str.strip())


def compute_constant_facts(function_data, max_facts: int = 25) -> list[dict]:
    """
    Bounded, intraprocedural constant propagation over the function's own
    p-code. Explicitly NOT symbolic execution: this is a single forward
    linear pass over Ghidra's high p-code, which is already in SSA form
    (each varnode has exactly one defining op) — so propagating a literal
    through a COPY chain, or folding simple arithmetic once both operands
    are already known constants, needs no dominance/merge/path reasoning at
    all. No branches are evaluated and nothing crosses a function boundary.
    """
    pcode = function_data.pcode if hasattr(function_data, "pcode") else function_data.get("pcode", [])

    known: dict = {}
    facts = []

    for raw in pcode:
        try:
            mnemonic, output, inputs = _split_pcode_op(raw)
        except Exception:
            continue
        if output is None:
            continue

        value = None
        if mnemonic == "COPY" and len(inputs) == 1:
            value = _const_value(inputs[0], known)
        elif mnemonic in _CONST_FOLD and len(inputs) == 2:
            a = _const_value(inputs[0], known)
            b = _const_value(inputs[1], known)
            if a is not None and b is not None:
                try:
                    value = _CONST_FOLD[mnemonic](a, b)
                except Exception:
                    value = None

        if value is not None:
            known[output] = value
            if len(facts) < max_facts:
                facts.append({"varnode": output, "value": value, "op": mnemonic})

    return facts


_STACK_LOCAL_RE = re.compile(r'\blocal_[0-9a-fA-F]+\b')
_ALLOC_CALL_RE = re.compile(r'\b(malloc|calloc|operator\.new|operator_new)\s*\(')


def compute_alias_hints(function_data) -> dict:
    """
    Partial pointer-aliasing heuristic: distinct Ghidra-named stack locals
    are guaranteed-distinct storage (each has its own frame offset, so two
    different local_X names can never be the same slot), and each
    malloc/operator-new call site is a distinct heap allocation from every
    other call site in the same function. This is NOT general alias
    analysis (no escape analysis, no pointer-provenance tracking) — it only
    records what is *guaranteed distinct*, never a claim about what *does*
    alias.
    """
    decompiled = function_data.decompiled if hasattr(function_data, "decompiled") else function_data.get("decompiled", "")
    decompiled = decompiled or ""
    stack_locals = sorted(set(_STACK_LOCAL_RE.findall(decompiled)))
    alloc_sites = len(_ALLOC_CALL_RE.findall(decompiled))
    return {"distinct_stack_locals": stack_locals, "distinct_alloc_call_sites": alloc_sites}


def analyze_deterministic(function_data) -> list[dict]:
    """
    Pure static-analysis facts for one function — no AI involved. Returns a
    list of {fact_type, value, evidence} dicts (value is JSON-serializable),
    ready for the caller to attach an entity_id/confidence and pass to
    SemanticDB.record_facts_batch(). Kept DB-agnostic on purpose, matching
    this module's existing design (build_cfg_summary has never imported
    types_db either).
    """
    facts = []

    conv = extract_calling_convention(function_data)
    if conv:
        facts.append({"fact_type": "calling_convention", "value": conv,
                       "evidence": ["ghidra_signature"]})

    loops = compute_natural_loops(function_data)
    if loops:
        facts.append({"fact_type": "natural_loops", "value": json.dumps(loops),
                       "evidence": ["ghidra_cfg_dominators"]})

    consts = compute_constant_facts(function_data)
    if consts:
        facts.append({"fact_type": "propagated_constants", "value": json.dumps(consts),
                       "evidence": ["ghidra_pcode_ssa"]})

    alias = compute_alias_hints(function_data)
    if alias["distinct_stack_locals"] or alias["distinct_alloc_call_sites"]:
        facts.append({"fact_type": "alias_hints", "value": json.dumps(alias),
                       "evidence": ["ghidra_decompiled_text"]})

    return facts
