"""
Load and validate the JSON exported by ExportAnalysis.java into typed
pydantic models that the rest of the pipeline can consume.
"""

import json
from pydantic import BaseModel, ConfigDict, Field


class Parameter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    type_name: str = Field(alias="type", default="")


class CalleeRef(BaseModel):
    """Address-qualified callee — distinguishes two same-named functions
    within one binary, which the plain `callees` name list cannot."""
    name: str
    address: str


class CFGBlock(BaseModel):
    address: str
    end: str = ""
    instructions: list[str] = []


class CFGEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_addr: str = Field(alias="from")
    to_addr: str = Field(alias="to")
    flow_type: str = Field(alias="flowType", default="")


class CFG(BaseModel):
    blocks: list[CFGBlock] = []
    edges: list[CFGEdge] = []


class FunctionData(BaseModel):
    """One function exported from Ghidra."""
    name: str
    address: str
    signature: str = ""
    parameters: list[Parameter] = []
    callees: list[str] = []
    callee_refs: list[CalleeRef] = Field(alias="calleeRefs", default=[])
    callers: list[str] = []
    imports: list[str] = []
    decompiled: str = ""
    pcode: list[str] = []
    cfg: CFG = Field(default_factory=CFG)
    strings: list[str] = []

    def to_context_dict(self) -> dict:
        """Flat dict for passing to AI translator / prompt builder."""
        return self.model_dump(by_alias=False)


def load_analysis(json_path: str) -> list[FunctionData]:
    """
    Parse the Ghidra JSON export and return a list of FunctionData objects.
    Invalid / incomplete entries are skipped with a warning rather than crashing.
    """
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)

    results: list[FunctionData] = []
    skipped = 0

    for entry in raw:
        try:
            results.append(FunctionData.model_validate(entry))
        except Exception as exc:
            name = entry.get("name", "<unknown>") if isinstance(entry, dict) else "?"
            print(f"[parse_output] WARNING: skipping {name}: {exc}")
            skipped += 1

    if skipped:
        print(f"[parse_output] {skipped} functions skipped due to parse errors")

    return results
