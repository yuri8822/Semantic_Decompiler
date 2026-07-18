"""
Pipeline orchestration — Ghidra analysis -> load -> DB seed -> multi-pass AI
translation -> write output. UI-agnostic: progress/status is reported via
on_event() callbacks (plain dataclasses, no rich/Textual dependency) instead
of being printed directly, so the same run_pipeline() drives both the CLI
(main.py) and the Textual TUI (tui/).
"""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from config import (
    DB_PATH, GHIDRA_JSON_DIR, NUM_PASSES, OUTPUT_DIR, LLM_PROVIDER, OLLAMA_MODEL,
    OUTPUT_WRITE_EVERY_N_FUNCTIONS, OUTPUT_WRITE_EVERY_SECONDS,
)
from analyzer.ghidra_runner import analyze_binary
from analyzer.parse_output import load_analysis
from analyzer.ir_builder import build_ir
from analyzer.cfg_builder import build_cfg_summary
from analyzer.types_db import SemanticDB
from analyzer.known_apis import KNOWN_APIS
from ai.translator import MultiPassTranslator, extract_function_name
from output.writer import ProjectWriter


@dataclass
class PipelineOptions:
    binary_path: str
    skip_ghidra: bool = False
    force_ghidra: bool = False
    restart: bool = False
    num_passes: int = NUM_PASSES
    output_dir: str = str(OUTPUT_DIR)
    limit: int = 0
    provider: str = LLM_PROVIDER
    ollama_model: str = OLLAMA_MODEL
    verbose: bool = False


# ---------------------------------------------------------------------------
# Events — plain dataclasses, no rich/Textual dependency, so this module can
# be imported by either presentation layer without pulling the other in.
# ---------------------------------------------------------------------------

@dataclass
class StageEvent:
    stage: str
    message: str
    level: str = "info"  # "info" | "warn" | "error"


@dataclass
class LogLine:
    text: str  # raw Ghidra subprocess output line


@dataclass
class ProgressEvent:
    completed: int
    total: int
    function_name: str
    cached: bool = False


@dataclass
class DoneEvent:
    output_path: str
    cancelled: bool = False


@dataclass
class ErrorEvent:
    message: str


def check_env(provider: str) -> None:
    checks = {
        "anthropic": ("ANTHROPIC_API_KEY", "sk-ant-..."),
        "xiaomi":    ("XIAOMI_API_KEY",     "sk-s..."),
    }
    if provider in checks:
        var, example = checks[provider]
        if not os.environ.get(var):
            raise EnvironmentError(
                f"{var} is not set. Add it to your .env file:  {var}={example}"
            )


def resolve_json_path(binary_path: Path) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", binary_path.name)
    return str(GHIDRA_JSON_DIR / (safe_name + ".json"))


def run_pipeline(
    options: PipelineOptions,
    on_event: Callable[[object], None],
    should_cancel: Callable[[], bool] = lambda: False,
) -> Path:
    """
    Runs the full pipeline for a single binary. Raises FileNotFoundError,
    RuntimeError, or EnvironmentError on the same fatal conditions that used
    to sys.exit(1) in the old CLI-only main() — callers decide how to
    present that. should_cancel() is checked once per function (between
    functions, not between a function's 6 AI passes); on a true result the
    writer is flushed and a DoneEvent(cancelled=True) is returned rather
    than raising, since cancellation isn't an error.
    """
    check_env(options.provider)

    binary_path = Path(options.binary_path)
    # Identifies this binary's rows in semantic.db — addresses alone aren't
    # globally unique (two different binaries can share a load address, and
    # commonly do for native PE executables), so every DB lookup needs both.
    binary_name = binary_path.stem

    # ------------------------------------------------------------------
    # Step 1: Ghidra analysis
    # ------------------------------------------------------------------
    json_path = resolve_json_path(binary_path)
    export_exists = Path(json_path).exists()

    # Staleness check: if the binary itself is newer than its export (e.g.
    # it was recompiled since), auto-reuse would otherwise silently analyze
    # stale data with zero indication anything changed. Only meaningful when
    # the binary is actually on disk to compare against — reusing an export
    # without the original binary present (a normal --skip-ghidra workflow)
    # has nothing to compare, so it's left alone.
    export_stale = (
        export_exists and binary_path.exists()
        and binary_path.stat().st_mtime > Path(json_path).stat().st_mtime
    )

    if options.force_ghidra or options.restart or (not options.skip_ghidra and (not export_exists or export_stale)):
        on_event(StageEvent("ghidra", "Running Ghidra headless analysis..."))
        try:
            json_path = analyze_binary(
                str(binary_path), verbose=options.verbose,
                on_line=lambda line: on_event(LogLine(line.rstrip("\n"))),
            )
        except RuntimeError as e:
            raise RuntimeError(f"Ghidra failed:\n{e}") from e
        on_event(StageEvent("ghidra", f"Export written to {json_path}"))
    else:
        # Resume support: reuse an existing export automatically — Ghidra
        # analysis is the slowest, most re-runnable-for-no-reason step in
        # the pipeline. --skip-ghidra remains valid for explicitness; a
        # missing export still errors out the same way it always has.
        if export_stale:
            on_event(StageEvent(
                "ghidra",
                f"Warning: {binary_path.name} looks newer than its Ghidra export "
                f"(skipping anyway since --skip-ghidra was explicit). Pass "
                f"--force-ghidra for a fresh analysis if you've recompiled it.",
                level="warn",
            ))
        reason = "requested via --skip-ghidra" if options.skip_ghidra else "existing export found"
        on_event(StageEvent("ghidra", f"Skipping Ghidra ({reason}) — using {json_path}"))
        if not Path(json_path).exists():
            raise FileNotFoundError(f"JSON not found at {json_path}")

    # ------------------------------------------------------------------
    # Step 2: Load + validate
    # ------------------------------------------------------------------
    on_event(StageEvent("load", "Loading Ghidra export..."))
    functions = load_analysis(json_path)

    if options.limit:
        functions = functions[:options.limit]
        on_event(StageEvent("load", f"(limited to first {options.limit} functions)"))

    on_event(StageEvent("load", f"{len(functions)} functions loaded"))

    # ------------------------------------------------------------------
    # Step 3: Seed the database
    # ------------------------------------------------------------------
    on_event(StageEvent("db", "Initializing semantic database..."))
    db = SemanticDB(DB_PATH)
    db.init()

    for fn in functions:
        db.upsert_function(binary_name, fn.address, fn.name, fn.signature)
        for callee in fn.callees:
            db.add_call_edge(binary_name, fn.address, callee)

    db.seed_known_apis(KNOWN_APIS)

    # address -> name map for IR CALL annotation (lowercase hex, no 0x prefix)
    addr_map = {fn.address.lower(): fn.name for fn in functions}

    on_event(StageEvent("db", f"Database ready at {DB_PATH}"))

    # ------------------------------------------------------------------
    # Step 4: Multi-pass AI reconstruction
    # ------------------------------------------------------------------
    on_event(StageEvent("translate", f"AI reconstruction ({options.num_passes} passes per function)..."))

    translator = MultiPassTranslator(
        db=db,
        binary_name=binary_name,
        num_passes=options.num_passes,
        provider=options.provider,
        ollama_model=options.ollama_model,
        restart=options.restart,
    )
    writer = ProjectWriter(output_dir=options.output_dir, binary_name=binary_name)

    # Throttle writer.write() instead of calling it after every function —
    # it fully re-serializes everything accumulated so far, so calling it
    # unconditionally is O(n^2) over a large run. Writes at most every
    # OUTPUT_WRITE_EVERY_N_FUNCTIONS functions or OUTPUT_WRITE_EVERY_SECONDS
    # seconds, whichever comes first; the unconditional final write after
    # the loop always catches whatever this misses.
    since_last_write = 0
    last_write_time = time.monotonic()

    def _write_if_due():
        nonlocal since_last_write, last_write_time
        since_last_write += 1
        due = (since_last_write >= OUTPUT_WRITE_EVERY_N_FUNCTIONS
               or time.monotonic() - last_write_time >= OUTPUT_WRITE_EVERY_SECONDS)
        if due:
            writer.write()
            since_last_write = 0
            last_write_time = time.monotonic()

    total = len(functions)
    for i, fn in enumerate(functions):
        if should_cancel():
            writer.write()
            on_event(DoneEvent(output_path=str(writer.out_root), cancelled=True))
            return writer.out_root

        # Resume support: a prior run may have already fully translated
        # this function with this exact provider — reuse it instead of
        # burning another 6-pass round trip. A result from a *different*
        # provider doesn't count; switching providers means you want
        # fresh output, not someone else's cached answer.
        if not options.restart and db.is_complete_for_provider(binary_name, fn.address, options.provider.lower()):
            cached = db.get_function(binary_name, fn.address)
            ai_name = cached.get("ai_name") or fn.name
            writer.add_function(ai_name, fn.address, cached["final_cpp"], fn.signature)
            _write_if_due()
            on_event(ProgressEvent(completed=i + 1, total=total, function_name=fn.name, cached=True))
            continue

        ir          = build_ir(fn, addr_map=addr_map)
        cfg_summary = build_cfg_summary(fn)
        api_context = db.get_api_context(fn.imports)

        cpp = translator.translate(
            function_data=fn.to_context_dict(),
            ir=ir,
            cfg_summary=cfg_summary,
            api_context=api_context,
        )

        ai_name = extract_function_name(cpp, fn.name)
        writer.add_function(ai_name, fn.address, cpp, fn.signature)
        _write_if_due()
        on_event(ProgressEvent(completed=i + 1, total=total, function_name=fn.name, cached=False))

    # ------------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------------
    out_path = writer.write()
    on_event(DoneEvent(output_path=str(out_path), cancelled=False))
    return out_path
