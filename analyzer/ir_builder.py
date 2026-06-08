"""
Converts raw Ghidra p-code strings (from PcodeOpAST.toString()) into a
clean, human-readable IR that is easier for the AI to reason about.

P-code string format (from Ghidra source):
    <output_varnode> = OPNAME <input0> , <input1> ...
    --- = OPNAME <input0> , <input1> ...  (no output)

Varnode format:
    (spacename,0xOFFSET,SIZE)
"""

import re
from typing import Optional

# Matches a single varnode token: (space,offset,size)
_VARNODE_RE = re.compile(r'\((\w+),(0x[0-9a-fA-F]+|\d+),(\d+)\)')

# Human-readable symbol for arithmetic/comparison mnemonics
_BINARY_OPS = {
    "INT_ADD": "+",  "INT_SUB": "-",  "INT_MULT": "*",  "INT_DIV": "/",
    "INT_REM": "%",  "INT_AND": "&",  "INT_OR":  "|",   "INT_XOR": "^",
    "INT_LEFT": "<<", "INT_RIGHT": ">>", "INT_SRIGHT": ">>s",
    "INT_EQUAL": "==", "INT_NOTEQUAL": "!=",
    "INT_LESS": "<u", "INT_LESSEQUAL": "<=u",
    "INT_SLESS": "<",  "INT_SLESSEQUAL": "<=",
    "FLOAT_ADD": "+f", "FLOAT_SUB": "-f", "FLOAT_MULT": "*f", "FLOAT_DIV": "/f",
    "FLOAT_EQUAL": "==f", "FLOAT_LESS": "<f",
    "BOOL_AND": "&&", "BOOL_OR": "||", "BOOL_XOR": "^^",
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_varnode(s: str) -> Optional[dict]:
    m = _VARNODE_RE.match(s.strip())
    if not m:
        return None
    return {"space": m.group(1), "offset": m.group(2), "size": int(m.group(3))}


def _parse_op(raw: str) -> dict:
    raw = raw.strip()
    output: Optional[dict] = None
    rest = raw

    # Determine if there's an output varnode
    m = _VARNODE_RE.match(raw)
    if m:
        output = {"space": m.group(1), "offset": m.group(2), "size": int(m.group(3))}
        rest = raw[m.end():].strip()
    elif raw.startswith("---"):
        rest = raw[3:].strip()

    # Strip the '= ' separator that Ghidra places between output and mnemonic
    if rest.startswith("="):
        rest = rest[1:].strip()

    # Extract mnemonic (first word)
    parts = rest.split(None, 1)
    if not parts:
        return {"mnemonic": "UNKNOWN", "output": output, "inputs": [], "raw": raw}

    mnemonic = parts[0]
    inputs: list[dict] = []

    if len(parts) > 1:
        # Split on ' , ' (Ghidra separates inputs with ' , ')
        for tok in re.split(r'\s*,\s*', parts[1]):
            vn = _parse_varnode(tok.strip())
            if vn:
                inputs.append(vn)

    return {"mnemonic": mnemonic, "output": output, "inputs": inputs, "raw": raw}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_varnode(vn: Optional[dict]) -> str:
    if vn is None:
        return "?"
    space, offset, size = vn["space"], vn["offset"], vn["size"]
    if space == "const":
        return offset
    elif space == "register":
        return f"r{offset}_{size}"
    elif space == "unique":
        # Use only the last 4 hex digits to keep names short
        short = offset[-4:] if len(offset) > 4 else offset
        return f"t{short}_{size}"
    elif space == "ram":
        return f"[{offset}]"
    else:
        return f"{space}_{offset}"


def _fmt_op(op: dict) -> str:
    mn = op["mnemonic"]
    out = op["output"]
    ins = op["inputs"]
    in_s = [_fmt_varnode(v) for v in ins]

    # Helper for two-input form
    def binop(sym: str) -> str:
        a = in_s[0] if in_s else "?"
        b = in_s[1] if len(in_s) > 1 else "?"
        return f"  {_fmt_varnode(out)} = {a} {sym} {b}"

    if out:
        out_str = _fmt_varnode(out)

        if mn in _BINARY_OPS:
            return binop(_BINARY_OPS[mn])

        if mn == "COPY":
            src = in_s[0] if in_s else "?"
            return f"  {out_str} = {src}"

        if mn == "LOAD":
            # LOAD space , address  →  out = *(address)
            addr = in_s[1] if len(in_s) > 1 else (in_s[0] if in_s else "?")
            return f"  {out_str} = *({addr})"

        if mn in ("INT_ZEXT", "INT_SEXT", "INT_TRUNC", "FLOAT_FLOAT2FLOAT",
                  "FLOAT_INT2FLOAT", "FLOAT_TRUNC", "INT_NEGATE", "INT_2COMP",
                  "BOOL_NEGATE", "FLOAT_NEG", "FLOAT_ABS", "FLOAT_SQRT",
                  "POPCOUNT", "LZCOUNT"):
            src = in_s[0] if in_s else "?"
            return f"  {out_str} = {mn}({src})"

        if mn in ("CALL", "CALLIND"):
            target = in_s[0] if in_s else "?"
            args = ", ".join(in_s[1:])
            return f"  {out_str} = CALL {target}({args})"

        if mn == "PIECE":
            return f"  {out_str} = CONCAT({', '.join(in_s)})"

        if mn in ("SUBPIECE",):
            src = in_s[0] if in_s else "?"
            off = in_s[1] if len(in_s) > 1 else "0"
            return f"  {out_str} = {src}[{off}:{out['size']}]"

        # Generic fallback with output
        return f"  {out_str} = {mn}({', '.join(in_s)})"

    else:
        # Void operations
        if mn == "STORE":
            # STORE space , addr , value
            addr  = in_s[1] if len(in_s) > 1 else "?"
            value = in_s[2] if len(in_s) > 2 else (in_s[0] if in_s else "?")
            return f"  *({addr}) = {value}"

        if mn in ("CALL", "CALLIND"):
            target = in_s[0] if in_s else "?"
            args = ", ".join(in_s[1:])
            return f"  CALL {target}({args})"

        if mn == "BRANCH":
            return f"  GOTO {in_s[0] if in_s else '?'}"

        if mn == "CBRANCH":
            dest = in_s[0] if in_s else "?"
            cond = in_s[1] if len(in_s) > 1 else "?"
            return f"  IF ({cond}) GOTO {dest}"

        if mn == "BRANCHIND":
            return f"  GOTO *({in_s[0] if in_s else '?'})"

        if mn == "RETURN":
            val = in_s[1] if len(in_s) > 1 else (in_s[0] if in_s else "void")
            return f"  RETURN {val}"

        if mn == "CALLOTHER":
            return f"  SYSCALL({', '.join(in_s)})"

        # Generic void fallback
        return f"  {mn} {', '.join(in_s)}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ir(function_data, addr_map: dict | None = None) -> str:
    """
    Build a human-readable IR from a FunctionData object or plain dict.
    Returns the IR as a multi-line string ready to inject into an AI prompt.

    addr_map: optional {hex_address_str: function_name} mapping used to
              annotate CALL targets with their resolved names, e.g.
              "CALL FUN_140001010@[0x140001010]" instead of "CALL [0x140001010]".
              Keys should be lowercase hex without the 0x prefix.
    """
    if hasattr(function_data, "name"):
        name      = function_data.name
        signature = function_data.signature
        pcode     = function_data.pcode
    else:
        name      = function_data.get("name", "?")
        signature = function_data.get("signature", "")
        pcode     = function_data.get("pcode", [])

    lines = [f"FUNCTION: {name}", f"SIGNATURE: {signature}", ""]

    for raw_op in pcode:
        try:
            op = _parse_op(raw_op)
            lines.append(_fmt_op(op))
        except Exception:
            lines.append(f"  ; {raw_op}")

    ir = "\n".join(lines)

    if addr_map:
        ir = _annotate_calls(ir, addr_map)

    return ir


def _annotate_calls(ir: str, addr_map: dict) -> str:
    """
    Replace bare address tokens in CALL lines with 'Name@[address]'.
    Only touches lines that already contain the word CALL to avoid
    annotating plain memory reads/writes.
    """
    result = []
    for line in ir.split("\n"):
        if " CALL " in line or line.strip().startswith("CALL "):
            line = _ADDR_RE.sub(lambda m: _resolve_addr(m, addr_map), line)
        result.append(line)
    return "\n".join(result)


_ADDR_RE = re.compile(r'\[0x([0-9a-fA-F]+)\]')


def _resolve_addr(match: re.Match, addr_map: dict) -> str:
    hex_digits = match.group(1).lower()
    name = addr_map.get(hex_digits) or addr_map.get(hex_digits.lstrip("0") or "0")
    if name:
        return f"{name}@{match.group(0)}"
    return match.group(0)
