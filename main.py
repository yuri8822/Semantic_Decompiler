"""
AI-Assisted Semantic Decompiler
Entry point — orchestrates the full pipeline from binary to C++.

Usage:
    python main.py <binary> [options]

    --skip-ghidra   Use an existing data/ghidra_json/<binary>.json export
    --passes N      Number of AI refinement passes (1-6, default 6)
    --output DIR    Output directory (default: output/recovered)
    --limit N       Process only the first N functions (0 = all)
    --verbose       Show Ghidra output during analysis
"""

import argparse
import os
import re
import sys
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

from config import DB_PATH, GHIDRA_JSON_DIR, NUM_PASSES, OUTPUT_DIR, LLM_PROVIDER, OLLAMA_MODEL
from analyzer.ghidra_runner import analyze_binary
from analyzer.parse_output import load_analysis
from analyzer.ir_builder import build_ir
from analyzer.cfg_builder import build_cfg_summary
from analyzer.types_db import SemanticDB
from analyzer.known_apis import KNOWN_APIS
from ai.translator import MultiPassTranslator
from output.writer import ProjectWriter

console = Console()


_NAME_SKIP = frozenset({
    "if", "else", "while", "for", "switch", "do", "return",
    "struct", "class", "enum", "typedef", "extern", "static", "inline",
})


def _extract_ai_name(cpp: str, fallback: str) -> str:
    """
    Extract only the function name from AI-generated code.
    Much simpler than extracting the full signature: just find the identifier
    immediately before '(' on the first function definition line.
    """
    in_comment = False
    lines = cpp.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if in_comment:
            if "*/" in s:
                in_comment = False
            continue
        if s.startswith("/*"):
            if "*/" not in s:
                in_comment = True
            continue
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        if re.match(r'^(struct|class|enum|typedef|extern)\b', s):
            continue
        if "(" not in s:
            continue
        # Must be a definition: has { on this line or the very next
        has_brace = "{" in s or (
            i + 1 < len(lines) and lines[i + 1].strip().startswith("{")
        )
        if not has_brace:
            continue
        # Skip forward declarations (ends with ; but no {)
        sig = s.split("{")[0].rstrip()
        if sig.endswith(";"):
            continue
        before_paren = sig.split("(")[0].rstrip()
        m = re.search(r'\b([A-Za-z_]\w*)\s*$', before_paren)
        if m and m.group(1) not in _NAME_SKIP:
            return m.group(1)
    return fallback


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
        help="Skip Ghidra analysis (use existing JSON export)"
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
        "--provider", default=LLM_PROVIDER, choices=["anthropic", "xiaomi", "ollama"],
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
    if not args.skip_ghidra:
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
        json_path = _resolve_json_path(binary_path)
        console.print(f"\n[bold][1/4][/bold] [dim]Skipping Ghidra — using {json_path}[/dim]")
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

    for fn in functions:
        db.upsert_function(fn.address, fn.name, fn.signature)
        for callee in fn.callees:
            db.add_call_edge(fn.address, callee)

    db.seed_known_apis(KNOWN_APIS)

    # address → name map for IR CALL annotation (lowercase hex, no 0x prefix)
    addr_map = {fn.address.lower(): fn.name for fn in functions}

    console.print(f"  [green]✓[/green] Database ready at {DB_PATH}")

    # ----------------------------------------------------------------
    # Step 4: Multi-pass AI reconstruction
    # ----------------------------------------------------------------
    console.print(f"\n[bold][4/4][/bold] AI reconstruction ({args.passes} passes per function)...\n")

    translator = MultiPassTranslator(
        db=db,
        num_passes=args.passes,
        provider=args.provider,
        ollama_model=args.ollama_model,
    )
    writer = ProjectWriter(output_dir=args.output, binary_name=binary_path.stem)

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

            ai_name = _extract_ai_name(cpp, fn.name)
            writer.add_function(ai_name, fn.address, cpp, fn.signature)
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
