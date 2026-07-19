"""
AI-Assisted Semantic Decompiler
Entry point — orchestrates the full pipeline from binary to C++.

Usage:
    python main.py <binary> [options]

    --skip-ghidra   Use an existing data/ghidra_json/<binary>.json export
                    (an existing export is now reused automatically anyway —
                    this remains for explicitness / backwards compatibility)
    --force-ghidra  Re-run Ghidra even if an export already exists
    --restart       Full fresh run: ignore all resume/cache state for this
                     binary — re-runs Ghidra and reprocesses every function
                     from pass 1, even ones already completed by this provider
    --passes N      Number of AI refinement passes (1-6, default 6)
    --output DIR    Output directory (default: output/recovered)
    --limit N       Process only the first N functions (0 = all)
    --verbose       Show Ghidra output during analysis
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from config import (
    DB_PATH, GHIDRA_JSON_DIR, NUM_PASSES, OUTPUT_DIR, LLM_PROVIDER, OLLAMA_MODEL,
    OUTPUT_WRITE_EVERY_N_FUNCTIONS, OUTPUT_WRITE_EVERY_SECONDS,
)
from analyzer.ghidra_runner import analyze_binary
from analyzer.parse_output import load_analysis
from analyzer.ir_builder import build_ir
from analyzer.cfg_builder import build_cfg_summary, analyze_deterministic
from analyzer.types_db import SemanticDB, compute_confidence
from analyzer.known_apis import KNOWN_APIS
from analyzer.library_signatures import detect_library_types, classify_known_apis
from ai.translator import MultiPassTranslator, extract_function_name
from output.writer import ProjectWriter

console = Console()


def _check_env(provider: str):
    checks = {
        "anthropic": ("ANTHROPIC_API_KEY", "sk-ant-..."),
        "xiaomi":    ("XIAOMI_API_KEY",     "sk-s..."),
    }
    if provider in checks:
        var, example = checks[provider]
        if not os.environ.get(var):
            console.print(
                f"[bold red]ERROR:[/bold red] {var} is not set.\n"
                f"Add it to your .env file:  {var}={example}"
            )
            sys.exit(1)


def _resolve_json_path(binary_path: Path) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", binary_path.name)
    return str(GHIDRA_JSON_DIR / (safe_name + ".json"))


def main():
    parser = argparse.ArgumentParser(
        description="AI-Assisted Semantic Decompiler — binary → idiomatic C++",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("binary", help="Path to the binary to analyze")
    parser.add_argument(
        "--skip-ghidra", action="store_true",
        help="Skip Ghidra analysis (use existing JSON export). An existing "
             "export is now reused automatically regardless of this flag; "
             "it remains for explicitness / backwards compatibility"
    )
    parser.add_argument(
        "--force-ghidra", action="store_true",
        help="Re-run Ghidra even if an export for this binary already exists"
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="Full fresh run: ignore all resume/cache state for this binary — "
             "re-runs Ghidra and reprocesses every function from pass 1, even "
             "ones already completed by this provider"
    )
    parser.add_argument(
        "--passes", type=int, default=NUM_PASSES,
        metavar=f"1-{NUM_PASSES}",
        help=f"Number of AI refinement passes (default: {NUM_PASSES})"
    )
    parser.add_argument(
        "--output", default=str(OUTPUT_DIR),
        help="Output directory for recovered C++ project"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Process only the first N functions (0 = all)"
    )
    parser.add_argument(
        "--provider", default=LLM_PROVIDER, choices=["anthropic", "xiaomi", "ollama", "bonsai"],
        help=f"LLM provider (default: {LLM_PROVIDER})"
    )
    parser.add_argument(
        "--ollama-model", default=OLLAMA_MODEL,
        metavar="MODEL",
        help=f"Ollama model name when --provider ollama (default: {OLLAMA_MODEL})"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Stream Ghidra output to stdout during analysis"
    )
    args = parser.parse_args()

    _check_env(args.provider)

    binary_path = Path(args.binary)
    # Identifies this binary's rows in semantic.db — addresses alone aren't
    # globally unique (two different binaries can share a load address, and
    # commonly do for native PE executables), so every DB lookup needs both.
    binary_name = binary_path.stem

    console.print(Panel(
        f"[bold]Binary:[/bold] {binary_path.name}\n"
        f"[bold]Passes:[/bold] {args.passes}   "
        f"[bold]Output:[/bold] {args.output}",
        title="[bold cyan]AI Semantic Decompiler[/bold cyan]",
        expand=False,
    ))

    # ----------------------------------------------------------------
    # Step 1: Ghidra analysis
    # ----------------------------------------------------------------
    json_path = _resolve_json_path(binary_path)
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

    if args.force_ghidra or args.restart or (not args.skip_ghidra and (not export_exists or export_stale)):
        console.print("\n[bold][1/4][/bold] Running Ghidra headless analysis...")
        try:
            json_path = analyze_binary(str(binary_path), verbose=args.verbose)
        except FileNotFoundError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            sys.exit(1)
        except RuntimeError as e:
            console.print(f"[bold red]Ghidra failed:[/bold red]\n{e}")
            sys.exit(1)
        console.print(f"  [green]✓[/green] Export written to {json_path}")
    else:
        # Resume support: reuse an existing export automatically — Ghidra
        # analysis is the slowest, most re-runnable-for-no-reason step in
        # the pipeline. --skip-ghidra remains valid for explicitness; a
        # missing export still errors out the same way it always has.
        if export_stale:
            console.print(
                f"[bold yellow]Warning:[/bold yellow] {binary_path.name} looks newer than its "
                f"Ghidra export (skipping anyway since --skip-ghidra was explicit). "
                f"Pass --force-ghidra for a fresh analysis if you've recompiled it."
            )
        reason = "requested via --skip-ghidra" if args.skip_ghidra else "existing export found"
        console.print(f"\n[bold][1/4][/bold] [dim]Skipping Ghidra ({reason}) — using {json_path}[/dim]")
        if not Path(json_path).exists():
            console.print(f"[bold red]Error:[/bold red] JSON not found at {json_path}")
            sys.exit(1)

    # ----------------------------------------------------------------
    # Step 2: Load + validate
    # ----------------------------------------------------------------
    console.print("\n[bold][2/4][/bold] Loading Ghidra export...")
    try:
        functions = load_analysis(json_path)
    except Exception as e:
        console.print(f"[bold red]Failed to load analysis:[/bold red] {e}")
        sys.exit(1)

    if args.limit:
        functions = functions[:args.limit]
        console.print(f"  [dim](limited to first {args.limit} functions)[/dim]")

    console.print(f"  [green]✓[/green] {len(functions)} functions loaded")

    # ----------------------------------------------------------------
    # Step 3: Seed the database
    # ----------------------------------------------------------------
    console.print("\n[bold][3/4][/bold] Initializing semantic database...")
    db = SemanticDB(DB_PATH)
    db.init()

    # First pass: upsert the function row, create a `function` entity per
    # function (entities must exist before relationships can reference
    # them), and record this function's deterministic evidence + any
    # detected library-type usage — all pure static analysis, no AI
    # involved, and nothing downstream reads any of it yet.
    entity_ids = {}
    type_entity_ids = {}
    for fn in functions:
        db.upsert_function(binary_name, fn.address, fn.name, fn.signature)
        for callee in fn.callees:
            db.add_call_edge(binary_name, fn.address, callee)
        entity_id = db.create_entity(binary_name, "function", fn.address)
        entity_ids[fn.address.lower()] = entity_id

        det_facts = analyze_deterministic(fn)
        if det_facts:
            db.record_facts_batch([
                {**f, "entity_id": entity_id,
                 "confidence": compute_confidence("deterministic", f.get("evidence")),
                 "provider": "deterministic"}
                for f in det_facts
            ])

        # Library-detection confidence (0.9 specific match / 0.6 generic
        # mangled-but-unrecognized, from library_signatures.py) reflects
        # match-specificity uncertainty, a different axis than the standard
        # deterministic/ai source-type formula — kept as its own explicit
        # value rather than forced through compute_confidence().
        for lib in detect_library_types(fn):
            type_key = lib["type_key"]
            type_id = type_entity_ids.get(type_key)
            if type_id is None:
                type_id = db.create_entity(binary_name, "type", type_key)
                type_entity_ids[type_key] = type_id
                db.record_fact(type_id, "library", lib["library"],
                                confidence=lib["confidence"], evidence=lib["evidence"],
                                provider="deterministic")
            db.add_relationship(binary_name, entity_id, type_id, "references",
                                 confidence=lib["confidence"], evidence=lib["evidence"])

    # Second pass: address-qualified `calls` relationships. Only recorded
    # when the callee resolves to one of this binary's own exported
    # functions (not an external/import) — this is what distinguishes two
    # same-named functions within one binary, which the name-only
    # call_graph edges above cannot.
    for fn in functions:
        src_id = entity_ids[fn.address.lower()]
        for ref in fn.callee_refs:
            dst_id = entity_ids.get(ref.address.lower())
            if dst_id is not None:
                db.add_relationship(binary_name, src_id, dst_id, "calls",
                                     confidence=0.9, evidence=["ghidra_call_graph"])

    db.seed_known_apis(KNOWN_APIS)
    db.tag_known_api_libraries(classify_known_apis())

    # address → name map for IR CALL annotation (lowercase hex, no 0x prefix)
    addr_map = {fn.address.lower(): fn.name for fn in functions}

    console.print(f"  [green]✓[/green] Database ready at {DB_PATH}")

    # ----------------------------------------------------------------
    # Step 4: Multi-pass AI reconstruction
    # ----------------------------------------------------------------
    console.print(f"\n[bold][4/4][/bold] AI reconstruction ({args.passes} passes per function)...\n")

    translator = MultiPassTranslator(
        db=db,
        binary_name=binary_name,
        num_passes=args.passes,
        provider=args.provider,
        ollama_model=args.ollama_model,
        restart=args.restart,
    )
    writer = ProjectWriter(output_dir=args.output, binary_name=binary_name)

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

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description:<40}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Reconstructing...", total=len(functions))

        for fn in functions:
            # Resume support: a prior run may have already fully translated
            # this function with this exact provider — reuse it instead of
            # burning another 6-pass round trip. A result from a *different*
            # provider doesn't count; switching providers means you want
            # fresh output, not someone else's cached answer.
            if not args.restart and db.is_complete_for_provider(binary_name, fn.address, args.provider.lower()):
                progress.update(task, description=f"[dim]{fn.name[:30]} (cached)[/dim]")
                cached = db.get_function(binary_name, fn.address)
                ai_name = cached.get("ai_name") or fn.name
                writer.add_function(ai_name, fn.address, cached["final_cpp"], fn.signature)
                _write_if_due()
                progress.advance(task)
                continue

            progress.update(task, description=f"[cyan]{fn.name[:38]}[/cyan]")

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
            progress.advance(task)

    # ----------------------------------------------------------------
    # Write output
    # ----------------------------------------------------------------
    out_path = writer.write()
    console.print(f"\n[bold green]Done.[/bold green] Recovered project written to: [bold]{out_path}[/bold]")
    console.print(f"  recovered.h   — type definitions + forward declarations")
    console.print(f"  recovered.cpp — full function implementations")
    console.print(f"  function_index.txt — address → name mapping")


if __name__ == "__main__":
    main()
