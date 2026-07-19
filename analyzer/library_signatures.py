"""
Best-effort known-library detection (architecture-mission Phase 2, mission
item #16 "recover libraries first"). Two independent things live here:

1. `detect_library_types()` — scans one function's own name and decompiled
   text for recognizable (already-demangled or partially-demangled) STL
   fragments, so a `type` entity + fact can be seeded *before* AI passes
   run — "this is std::vector<T>, don't reinvent it" instead of the model
   hallucinating library internals from scratch. This is pattern matching,
   not a real demangler: false negatives (missed STL usage) are expected
   and fine; false positives are what to avoid, so every pattern requires a
   fairly specific fragment, never a bare "std" substring.

2. `classify_known_apis()` — tags every name already in KNOWN_APIS
   (analyzer/known_apis.py) with a coarse library ("win32" or "crt"), for
   `known_apis.library` (added in Phase 1). Uses a naming-convention
   heuristic (Win32 APIs are conventionally PascalCase, CRT symbols are
   lower_snake or __prefixed) rather than a hand-maintained duplicate list —
   documented as a heuristic, not authoritative.
"""

import re

from analyzer.known_apis import KNOWN_APIS

# ---------------------------------------------------------------------------
# STL type detection
# ---------------------------------------------------------------------------

_STL_TYPE_PATTERNS = [
    (re.compile(r'std::basic_string|_Alloc_hider|_M_local_data|_M_dispose'), "std::basic_string"),
    (re.compile(r'std::vector|_M_realloc_insert'), "std::vector"),
    (re.compile(r'std::(unordered_)?map\b'), "std::map"),
    (re.compile(r'std::(unordered_)?set\b'), "std::set"),
    (re.compile(r'std::unique_ptr'), "std::unique_ptr"),
    (re.compile(r'std::shared_ptr|__shared_ptr'), "std::shared_ptr"),
    (re.compile(r'std::allocator\b'), "std::allocator"),
    (re.compile(r'std::pair\b'), "std::pair"),
]

# Structural (still-mangled) markers, used only when none of the specific
# fragments above matched — MSVC marks the std namespace as "@std@@";
# Itanium marks it as a length-prefixed "3std" component or the "St"
# substitution-table shorthand.
_MSVC_STD_RE = re.compile(r'^\?.*@std@@')
_ITANIUM_STD_RE = re.compile(r'^_Z.*(3std|St[0-9]?)')


def detect_library_types(function_data) -> list[dict]:
    """
    Returns a list of {type_key, library, evidence, confidence} dicts for
    STL types this function appears to reference. type_key is a stable
    string like "std::vector" suitable as a `type` entity's key.
    """
    name = function_data.name if hasattr(function_data, "name") else function_data.get("name", "")
    decompiled = function_data.decompiled if hasattr(function_data, "decompiled") else function_data.get("decompiled", "")
    haystack = f"{name}\n{decompiled or ''}"

    found = []
    seen_types = set()
    for pattern, type_name in _STL_TYPE_PATTERNS:
        if type_name not in seen_types and pattern.search(haystack):
            seen_types.add(type_name)
            found.append({"type_key": type_name, "library": "STL",
                          "evidence": ["stl_name_pattern"], "confidence": 0.9})

    if not seen_types and (_MSVC_STD_RE.match(name) or _ITANIUM_STD_RE.match(name)):
        # Structurally looks like a mangled STL symbol but no specific
        # fragment matched above — record a generic, lower-confidence marker
        # rather than nothing.
        found.append({"type_key": "std::<mangled>", "library": "STL",
                      "evidence": ["mangled_name_structure"], "confidence": 0.6})

    return found


# ---------------------------------------------------------------------------
# known_apis library classification
# ---------------------------------------------------------------------------

def _classify_name(name: str) -> str:
    """
    Win32 APIs are conventionally PascalCase (CreateFileA, VirtualAlloc);
    CRT symbols are lower_snake or __prefixed (malloc, __set_app_type).
    A naming-convention heuristic, not authoritative — good enough to
    populate known_apis.library without hand-maintaining a duplicate list.
    """
    if name[:1].isupper() and any(c.islower() for c in name):
        return "win32"
    return "crt"


def classify_known_apis() -> dict:
    """{name: 'win32'|'crt'} for every name currently in KNOWN_APIS."""
    return {name: _classify_name(name) for name in KNOWN_APIS}
