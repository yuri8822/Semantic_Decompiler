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


# ---------------------------------------------------------------------------
# STL-internal exclusion: real libstdc++ implementation members, statically
# linked into the binary — never legally redefinable as custom code, no
# matter how good the model or the prompt is.
# ---------------------------------------------------------------------------
#
# Confirmed on a real 101-function Chess.exe run (2026-07-29): every single
# STL-internal member Ghidra decompiled came back as a self-recursive stub —
# e.g. `void std::string::_M_dispose() { std::string::_M_dispose(); }` —
# because Ghidra can't resolve the real statically-linked libstdc++ symbol
# and just shows a self-reference instead. The model preserved that literally
# (correctly, per its own instructions not to alter calls it doesn't
# understand), but the result is either an ODR violation (redefining a type
# the real <string> already provides) or, if it somehow linked anyway,
# genuine infinite recursion. This isn't something a stricter prompt or a
# better model fixes — the real header already provides these, so they're
# skipped entirely rather than translated into misleading, broken bodies.
#
# MinGW CRT-startup internals (__tmainCRTStartup, __main, __do_global_ctors,
# etc.) get their own separate exclusion further down (is_mingw_crt_internal)
# for a different reason: unlike STL internals, Ghidra decompiles those with
# real, correct logic (confirmed on the same run) — they're excluded on
# scope policy (application logic only), not because the output is broken.
#
# Heuristic, not authoritative — false negatives (a real STL internal that
# slips through and gets translated anyway) are an acceptable, pre-existing
# risk; false positives (excluding real application code) are what to avoid,
# so this leans on naming conventions the C++ standard itself reserves to
# the implementation.

_STL_INTERNAL_NAME_RE = re.compile(r'^_[MS]_\w+$')  # _M_xxx / _S_xxx: the
# leading-underscore-plus-uppercase convention is reserved for the standard
# library implementation by the C++ standard itself.

_STL_INTERNAL_EXACT_NAMES = frozenset({
    "_Alloc_hider", "_Rep", "_Sp_counted_base", "_Guard",
    "new_allocator", "__new_allocator", "_Rep_base", "_Sp_counted_ptr",
})

_STL_FREE_FUNCTION_NAMES = frozenset({"getline"})
_STL_THROW_HELPER_RE = re.compile(r'^__throw_\w+$')  # __throw_logic_error,
# __throw_out_of_range, __throw_bad_alloc, ... — libstdc++'s internal
# exception-raising helpers, same "reserved to the implementation" rule.

_JUMPTABLE_WARNING_MARKERS = (
    "Could not recover jumptable", "Treating indirect jump as call",
)


def is_unresolved_self_reference(function_data) -> bool:
    """
    True iff this function's own body is Ghidra's own artifact of a
    statically-linked call it couldn't resolve, not real logic to
    translate -- a structural check, not a name list, so it generalizes
    to any unresolved-symbol case rather than needing each one hand-added
    (this replaced an earlier version of this exclusion that special-
    cased "operator.new"/"operator.new[]" by name; both are caught by
    this check too, without needing to know their names in advance).

    Confirmed against a real 101-function Chess.exe export: two
    independent signals from Ghidra's OWN output, both required together --
      1. This function's name appears in its own `callees` list (Ghidra's
         call-graph extraction resolved a call inside this function back
         to this function's own address).
      2. Its decompiled text carries Ghidra's own "Could not recover
         jumptable" / "Treating indirect jump as call" warning -- Ghidra's
         own admission that an indirect call/jump target couldn't be
         resolved and got decompiled as a direct call instead.
    Every STL-internal/operator-new self-reference in that export that
    would otherwise need hand-listing by name (_M_dispose, operator.new,
    __throw_logic_error, ...) satisfies both. Checking signal 1 alone
    would false-positive on real code: `atexit`'s printed C text looks
    self-recursive, but its `callees` metadata correctly names the real
    target (`_crt_atexit`), not itself, so it's simply a real thin
    wrapper -- not a break at all. Checking signal 1 without signal 2
    would ALSO false-positive: `std::char_traits<char>::length` lists
    itself in `callees` too, but its real body just calls `strlen()` --
    no self-call in the actual text, and correspondingly no warning
    either. Only functions satisfying both are the genuine artifact.
    """
    name = function_data.name if hasattr(function_data, "name") else function_data.get("name", "")
    callees = function_data.callees if hasattr(function_data, "callees") else function_data.get("callees", None)
    decompiled = (
        function_data.decompiled if hasattr(function_data, "decompiled")
        else function_data.get("decompiled", "")
    ) or ""
    if name not in (callees or []):
        return False
    return any(marker in decompiled for marker in _JUMPTABLE_WARNING_MARKERS)


_KNOWN_STL_CLASS_NAMES = frozenset({
    "string", "basic_string", "wstring", "vector", "map", "set",
    "unordered_map", "unordered_set", "allocator", "pair", "istream",
    "ostream", "iostream", "stringstream", "unique_ptr", "shared_ptr",
}) | _STL_INTERNAL_EXACT_NAMES

_STREAM_CLASS_NAMES = frozenset({
    "ostream", "istream", "iostream", "wostream", "wistream", "wiostream",
    "stringstream", "wstringstream", "ostringstream", "istringstream",
    "ofstream", "ifstream", "fstream",
})

# Types the real standard library already overloads operator<</operator>>
# for — if a Ghidra-decompiled `operator<<`/`operator>>` free function takes
# one of these alongside a stream, on either side, it is by definition not
# custom application logic: the real <ostream>/<istream>/<string> already
# provides that exact overload. (A genuine custom operator<< for an
# application type, e.g. `operator<<(ostream&, ChessPiece&)`, has a *non*-
# standard type on the other side and correctly stays out of this set.)
_STANDARD_STREAMABLE_TYPES = frozenset({
    "string", "wstring", "char", "wchar_t", "int", "uint", "long",
    "longlong", "ulonglong", "short", "float", "double", "bool", "void",
})


def _base_name(name: str) -> str:
    """Strip a trailing template-argument list Ghidra's demangler left
    attached, e.g. 'string<std::allocator<char>>' -> 'string',
    '_M_construct<char_const*>' -> '_M_construct'."""
    idx = name.find("<")
    return name[:idx] if idx != -1 else name


def _this_param_type(signature: str) -> str:
    """Bare type name of a Ghidra-decompiled `this` parameter, e.g.
    'undefined ~string(string * this)' -> 'string'."""
    m = re.search(r'\b(\w+)\s*\*\s*this\b', signature)
    return m.group(1) if m else ""


def _param_types(signature: str) -> list:
    """Bare type name of each parameter, in order, e.g.
    'ostream * operator<<(ostream * param_1, string * param_2)' ->
    ['ostream', 'string']."""
    m = re.search(r'\(([^)]*)\)', signature)
    if not m:
        return []
    types = []
    for param in m.group(1).split(","):
        m2 = re.match(r'\s*(\w+)', param)
        types.append(m2.group(1) if m2 else "")
    return types


def _is_standard_stream_operator(name: str, signature: str) -> bool:
    # Deliberately checking `name` itself, not `_base_name(name)` --
    # _base_name() strips at the first "<" to remove a template-argument
    # suffix (e.g. "string<...>" -> "string"), but "operator<<"'s own "<"
    # characters are the operator token itself, not template brackets;
    # running it through _base_name would truncate this down to just
    # "operator", matching nothing.
    if name not in ("operator<<", "operator>>"):
        return False
    types = [t.rstrip("*") for t in _param_types(signature)]
    if len(types) != 2:
        return False
    stream_side = any(t in _STREAM_CLASS_NAMES for t in types)
    standard_side = any(t in _STANDARD_STREAMABLE_TYPES for t in types)
    return stream_side and standard_side


def is_stl_internal_member(name: str, signature: str = "") -> bool:
    if _is_standard_stream_operator(name, signature):
        return True
    base = _base_name(name)
    if _STL_INTERNAL_NAME_RE.match(base):
        return True
    if base in _STL_INTERNAL_EXACT_NAMES or base in _STL_FREE_FUNCTION_NAMES:
        return True
    if _STL_THROW_HELPER_RE.match(base):
        return True
    if base.startswith("~") and base[1:] in _KNOWN_STL_CLASS_NAMES:
        return True
    if base in _KNOWN_STL_CLASS_NAMES:
        # A bare name exactly matching a real STL class (e.g. Ghidra's
        # "string" for a demangled std::string constructor) — real
        # application code is never named exactly "string"/"vector"/etc.
        return True
    this_type = _this_param_type(signature)
    if this_type in _KNOWN_STL_CLASS_NAMES:
        # `this` typed to a real STL class means this purports to be a
        # member of it (e.g. Ghidra's own `operator>>(istream * this, ...)`
        # for istream::operator>>) — can't legally be redefined either way,
        # regardless of what the bare name looks like.
        return True
    return False



# ---------------------------------------------------------------------------
# MinGW CRT-startup/runtime internals: not application logic either, widened
# back into scope on the user's explicit call (2026-07-30) even though these
# decompile with real, correct logic (confirmed on a real Chess.exe run) --
# unlike the STL-internal case, this exclusion isn't about broken output,
# it's a deliberate decision that the pipeline should only ever translate
# genuine application code, not CRT/runtime plumbing, regardless of how well
# that plumbing happens to decompile.
# ---------------------------------------------------------------------------

_MINGW_CRT_INTERNAL_NAMES = frozenset({
    # Entry points / startup sequence
    "_start", "__tmainCRTStartup", "mainCRTStartup", "WinMainCRTStartup",
    "pre_c_init", "pre_cpp_init", "__getmainargs", "__wgetmainargs", "__main",
    # CRT init/teardown
    "_initterm", "_initterm_e", "_cexit", "_exit", "_amsg_exit",
    "__do_global_ctors", "__do_global_dtors",
    "__gcc_register_frame", "__gcc_deregister_frame",
    "atexit", "_onexit", "_crt_atexit",
    # TLS / dynamic init
    "__dyn_tls_init", "__dyn_tls_dtor", "_pei386_runtime_relocator",
    "__mingw_TLScallback", "__mingwthr_run_key_dtors",
    "___w64_mingwthr_add_key_dtor", "___w64_mingwthr_remove_key_dtor",
    "__tlregdtor",
    # Exception / error handling
    "_gnu_exception_handler", "__mingw_oldexcpt_handler",
    "__mingw_invalidParameterHandler", "mingw_set_invalid_parameter_handler",
    "mingw_get_invalid_parameter_handler",
    "__mingw_setusermatherr", "__mingw_raise_matherr", "_matherr",
    "__setusermatherr", "_fpreset", "__report_error",
    # PE image introspection (used internally by CRT init -- TLS/exception setup)
    "_ValidateImageBase", "_FindPESection", "_FindPESectionByName",
    "_FindPESectionExec", "__mingw_GetSectionForAddress",
    "__mingw_GetSectionCount", "_GetPEImageBase",
    "_IsNonwritableInCurrentImage", "mark_section_writable",
    "__mingw_enum_import_library_names",
    # Misc CRT internals
    "___chkstk_ms", "__p__fmode", "__p__commode", "__p___initenv",
    "__acrt_iob_func", "__set_app_type", "_setargv",
})

_MINGW_CRT_NAME_PATTERNS = (
    re.compile(r'^__mingw_'),
    re.compile(r'^___'),
    re.compile(r'^__do_global_'),
)


def is_mingw_crt_internal(name: str) -> bool:
    return name in _MINGW_CRT_INTERNAL_NAMES or any(p.match(name) for p in _MINGW_CRT_NAME_PATTERNS)


def is_garbled_name(name: str) -> bool:
    """
    True if `name` contains a character that can never appear in a real,
    complete C++ identifier -- a bare "(" or ")" -- which only shows up
    when Ghidra's own name export truncated a deeply-qualified name (e.g.
    a local class nested inside a function-template instantiation) down to
    a bare fragment of its own signature. A real, observed case: a nested
    `std::string::_M_construct<...>::_Guard::_Guard(std::string*)`
    constructor exported with the name "string*)" -- not a real identifier
    under any circumstance, and not something any translation of it could
    ever compile as an out-of-line definition anyway (a local class's
    members can't be defined outside the function they're local to).
    """
    return "(" in name or ")" in name


def translation_exclusion_reason(function_data) -> str:
    """
    '' if this function is fine to translate normally. Otherwise a short
    human-readable reason it's being skipped entirely -- library/runtime
    code, not application logic, checked via four independent signals:
      1. A garbled/truncated name Ghidra itself couldn't cleanly export
         (see is_garbled_name) -- checked first since nothing else is
         worth checking if the name isn't even a real identifier.
      2. A real STL/libstdc++ internal member already provided by the real
         <string>/<vector>/etc. (name-based, see is_stl_internal_member).
      3. A structural Ghidra artifact -- a statically-linked call Ghidra
         couldn't resolve, decompiled as an artificial self-call rather
         than real logic (see is_unresolved_self_reference).
      4. A MinGW CRT-startup/runtime internal (see is_mingw_crt_internal)
         -- excluded on policy, not because it decompiles badly (most of
         these translate fine); the pipeline is scoped to application
         logic only, not CRT/runtime plumbing.
    """
    name = function_data.name if hasattr(function_data, "name") else function_data.get("name", "")
    signature = function_data.signature if hasattr(function_data, "signature") else function_data.get("signature", "")
    if is_garbled_name(name):
        return "Ghidra exported a garbled/truncated name (likely a function-local nested class member) -- not a real top-level symbol"
    if is_stl_internal_member(name, signature):
        return "STL/libstdc++/runtime internal -- provided by the real <string>/<vector>/<new>/etc."
    if is_unresolved_self_reference(function_data):
        return "Ghidra couldn't resolve this call/jump target -- decompiled as an artificial self-call, not real logic"
    if is_mingw_crt_internal(name):
        return "MinGW CRT/runtime internal -- provided by the real linked CRT, out of scope for application-logic translation"
    return ""
