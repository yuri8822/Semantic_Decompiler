# Ghidra test samples (Chess.exe)

Pure, unmodified Ghidra decompiler output — pulled straight from
`data/ghidra_json/Chess.exe.json`, before any AI translation touches them.
Each one exercises a different part of the pipeline (`ai/translator.py`'s
prompt, `analyzer/library_signatures.py`'s exclusion logic, or
`output/writer.py`'s class synthesis) so they're useful to feed through by
hand and check the result against.

---

## 1. `operator.new` — Ghidra's own unresolved-symbol self-call artifact

Address: `1400014e8`
Signature: `void * operator.new(ulonglong param_1)`
Callees (per Ghidra): `['operator.new']` — calls itself

```c
/* operator new(unsigned long long) */

void * operator_new(ulonglong param_1)

{
  void *pvVar1;
  
                    /* WARNING: Could not recover jumptable at 0x0001400014e8. Too many branches */
                    /* WARNING: Treating indirect jump as call */
  pvVar1 = operator_new(param_1);
  return pvVar1;
}
```

**What this tests:** `analyzer.library_signatures.is_unresolved_self_reference()`
should return `True` for this — its own name is in its own `callees` list
*and* the decompiled text carries Ghidra's "Could not recover jumptable" /
"Treating indirect jump as call" warning. This is what makes
`translation_exclusion_reason()` skip it entirely rather than asking the AI
to translate self-recursive nonsense.

---

## 2. `_M_dispose` — STL-internal, same self-call artifact by name

Address: `140001560`
Signature: `undefined _M_dispose(void)`
Callees (per Ghidra): `['_M_dispose']` — calls itself

```c
/* std::string::_M_dispose() */

void std::string::_M_dispose(void)

{
                    /* WARNING: Could not recover jumptable at 0x000140001560. Too many branches */
                    /* WARNING: Treating indirect jump as call */
  std::string::_M_dispose();
  return;
}
```

**What this tests:** Same structural self-call signal as #1, *and* it also
matches the name-based `is_stl_internal_member()` check (`_M_` prefix is
reserved to the standard library implementation). Good for confirming both
independent exclusion paths agree.

---

## 3. `Draw[abi:cxx11]` (King's) — the ABI-tag naming issue

Address: `140002840`
Signature: `King * Draw[abi:cxx11](King * this)`
Callees (per Ghidra): `['string<std::allocator<char>>', '~__new_allocator']`

```c
/* King::Draw[abi:cxx11]() */

King * __thiscall King::Draw_abi_cxx11_(King *this)

{
  allocator local_21;
  allocator *local_20;
  
  local_20 = &local_21;
  std::string::string<std::allocator<char>>((string *)this,"+",&local_21);
  std::__new_allocator<char>::~__new_allocator();
  return this;
}
```

**What this tests:** Ghidra's own decompiler already sanitizes the printed
identifier to `Draw_abi_cxx11_` (valid C++), but the *metadata* `signature`
field still spells it `Draw[abi:cxx11]` (the raw GCC ABI-version tag) — that
mismatch is exactly what broke `output/writer.py`'s `_declaration()` before
the fix (it used to fall through and emit the raw, invalid signature
verbatim into `recovered.h`). Also a real, still-present example of the
model's own broken output: `std::string::string<std::allocator<char>>(...)`
is not a valid explicit-constructor-call, and
`std::__new_allocator<char>::~__new_allocator();` calls a destructor with no
target object — both are bonsai model-quality issues, not pipeline bugs.

---

## 4. `King` (constructor) — base-class detection

Address: `1400028b0`
Signature: `undefined King(King * this)`
Callees (per Ghidra): `['Piece']`

```c
/* King::King() */

void __thiscall King::King(King *this)

{
  Piece::Piece((Piece *)this);
  *(undefined ***)this = &PTR_Draw_abi_cxx11__140009a60;
  this[0x10] = (King)0x1;
  return;
}
```

**What this tests:** `output/writer._detect_base_class()` should find
`Piece` as King's base from the `Piece::Piece((Piece *)this);` call in the
body — this is the exact function that exposed the bug where the
constructor's *own* signature line (`King::King(`) matched the detector's
regex before it ever reached the real base-class call, and the fix now
scopes that regex to the body only (after the opening `{`).

---

## 5. `Piece` (constructor) — no base class (regression check)

Address: `140005dc0`
Signature: `undefined Piece(Piece * this)`
Callees (per Ghidra): `[]`

```c
/* Piece::Piece() */

void __thiscall Piece::Piece(Piece *this)

{
  *(undefined ***)this = &PTR_Draw_abi_cxx11__140009ac0;
  this[0x10] = (Piece)0x0;
  *(undefined4 *)(this + 0x14) = 0;
  return;
}
```

**What this tests:** The base case for #4 — `Piece` has no base class, so
`_detect_base_class()` should correctly return `None` here rather than
guessing wrong. Also a plain root-class constructor: no inheritance, no STL
calls, useful as a clean baseline.

---

## 6. `Board` (constructor) — real application logic, nested loops + `new`

Address: `140002ec0`
Signature: `undefined Board(Board * this)`
Callees (per Ghidra): `['Piece', 'operator.new[]', 'operator.new']`

```c
/* Board::Board() */

void __thiscall Board::Board(Board *this)

{
  longlong lVar1;
  void *pvVar2;
  Piece *this_00;
  undefined4 local_20;
  undefined4 local_1c;
  
  pvVar2 = operator_new__(0x40);
  *(void **)this = pvVar2;
  for (local_1c = 0; local_1c < 8; local_1c = local_1c + 1) {
    lVar1 = *(longlong *)this;
    pvVar2 = operator_new__(0x40);
    *(void **)(lVar1 + (longlong)local_1c * 8) = pvVar2;
    for (local_20 = 0; local_20 < 8; local_20 = local_20 + 1) {
      this_00 = (Piece *)operator_new(0x18);
      Piece::Piece(this_00);
      *(Piece **)
       (*(longlong *)(*(longlong *)this + (longlong)local_1c * 8) + (longlong)local_20 * 8) =
           this_00;
    }
  }
  return;
}
```

**What this tests:** A meatier, genuinely interesting function — this is
the 8x8 board allocation (`Piece***` — array of pointers to arrays of
`Piece*`). Good one to run through `ai/translator.py`'s full prompt by hand
and judge whether the renaming/typing is actually good, since the logic is
non-trivial (nested loops, raw `operator_new` calls, pointer arithmetic)
without being STL/CRT noise.

---

## 7. `pre_c_init` — real CRT logic (now excluded from translation)

Address: `140001010`
Signature: `int pre_c_init(void)`
Callees (per Ghidra): `['_setargv', '_matherr', '__set_app_type', '__p__commode', '_amsg_exit', '__p__fmode', '__mingw_setusermatherr']`

```c
/* WARNING: Removing unreachable block (ram,0x000140001120) */
/* WARNING: Removing unreachable block (ram,0x00014000112a) */
/* WARNING: Unknown calling convention */

int pre_c_init(void)

{
  int iVar1;
  int *piVar2;
  
  __mingw_initltsdrot_force = 1;
  __mingw_initltsdyn_force = 1;
  __mingw_initltssuo_force = 1;
  managedapp = 0;
  if (__mingw_app_type == 0) {
    __set_app_type(1);
  }
  else {
    __set_app_type(2);
  }
  piVar2 = __p__fmode();
  *piVar2 = _fmode;
  piVar2 = __p__commode();
  *piVar2 = _commode;
  iVar1 = _setargv();
  if (-1 < iVar1) {
    if (_MINGW_INSTALL_DEBUG_MATHERR != 1) {
      return 0;
    }
    __mingw_setusermatherr(_matherr);
    return 0;
  }
                    /* WARNING: Subroutine does not return */
  _amsg_exit(8);
}
```

**What this tests:** `is_mingw_crt_internal("pre_c_init")` should return
`True` — this is a real, correctly-decompiled function (no self-call
artifact, genuine control flow) that's excluded purely on scope policy
(application logic only), not because it decompiles badly. Good contrast
case against #1/#2, which are excluded for the *opposite* reason (broken
output) despite also being library/runtime code.
