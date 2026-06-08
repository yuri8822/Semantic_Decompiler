"""
Builds a networkx DiGraph from the CFG exported by Ghidra and produces
a concise text summary for injection into the AI prompt.

Summary covers:
  - block / edge count
  - entry point
  - detected loops (back-edges via simple_cycles)
  - number of conditional branches
  - unreachable blocks (dead code)
"""

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
