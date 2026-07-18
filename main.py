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
import sys

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

from config import NUM_PASSES, OUTPUT_DIR, LLM_PROVIDER, OLLAMA_MODEL
from pipeline import (
    PipelineOptions, run_pipeline, check_env,
    StageEvent, LogLine, ProgressEvent, DoneEvent, ErrorEvent,
)

console = Console()

_STAGE_NUM = {"ghidra": 1, "load": 2, "db": 3, "translate": 4}


class _CLIReporter:
    """Recreates the CLI's console output from the pipeline's event stream."""

    def __init__(self, console: Console, verbose: bool = False):
        self.console = console
        self.verbose = verbose
        self._last_stage = None
        self._progress = None
        self._task = None

    def __call__(self, event) -> None:
        if isinstance(event, StageEvent):
            self._handle_stage(event)
        elif isinstance(event, LogLine):
            if self.verbose:
                print(event.text)
        elif isinstance(event, ProgressEvent):
            self._handle_progress(event)
        elif isinstance(event, DoneEvent):
            self._handle_done(event)
        elif isinstance(event, ErrorEvent):
            self._stop_progress()
            self.console.print(f"[bold red]Error:[/bold red] {event.message}")

    def _handle_stage(self, event: StageEvent) -> None:
        if event.level == "warn":
            self.console.print(f"[bold yellow]Warning:[/bold yellow] {event.message}")
            return
        is_new_stage = event.stage != self._last_stage
        self._last_stage = event.stage
        if is_new_stage:
            num = _STAGE_NUM.get(event.stage, "?")
            self.console.print(f"\n[bold][{num}/4][/bold] {event.message}")
        else:
            self.console.print(f"  [green]✓[/green] {event.message}")

    def _handle_progress(self, event: ProgressEvent) -> None:
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description:<40}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self.console,
            )
            self._progress.start()
            self._task = self._progress.add_task("Reconstructing...", total=event.total)
        desc = (
            f"[dim]{event.function_name[:30]} (cached)[/dim]" if event.cached
            else f"[cyan]{event.function_name[:38]}[/cyan]"
        )
        self._progress.update(self._task, description=desc)
        self._progress.advance(self._task)

    def _stop_progress(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def _handle_done(self, event: DoneEvent) -> None:
        self._stop_progress()
        if event.cancelled:
            if event.output_path:
                self.console.print(f"\n[bold yellow]Cancelled.[/bold yellow] Partial project written to: [bold]{event.output_path}[/bold]")
            else:
                self.console.print("\n[bold yellow]Cancelled.[/bold yellow] Nothing written yet.")
        else:
            self.console.print(f"\n[bold green]Done.[/bold green] Recovered project written to: [bold]{event.output_path}[/bold]")
            self.console.print("  recovered.h   — type definitions + forward declarations")
            self.console.print("  recovered.cpp — full function implementations")
            self.console.print("  function_index.txt — address → name mapping")


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

    try:
        check_env(args.provider)
    except EnvironmentError as e:
        console.print(f"[bold red]ERROR:[/bold red] {e}")
        sys.exit(1)

    options = PipelineOptions(
        binary_path=args.binary,
        skip_ghidra=args.skip_ghidra,
        force_ghidra=args.force_ghidra,
        restart=args.restart,
        num_passes=args.passes,
        output_dir=args.output,
        limit=args.limit,
        provider=args.provider,
        ollama_model=args.ollama_model,
        verbose=args.verbose,
    )

    console.print(Panel(
        f"[bold]Binary:[/bold] {args.binary}\n"
        f"[bold]Passes:[/bold] {args.passes}   "
        f"[bold]Output:[/bold] {args.output}",
        title="[bold cyan]AI Semantic Decompiler[/bold cyan]",
        expand=False,
    ))

    reporter = _CLIReporter(console, verbose=args.verbose)
    try:
        run_pipeline(options, on_event=reporter)
    except (FileNotFoundError, RuntimeError) as e:
        reporter._stop_progress()
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
