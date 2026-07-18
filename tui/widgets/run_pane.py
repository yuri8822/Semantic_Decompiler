"""Run tab: start/stop a pipeline run against a binary, watch live progress."""

from textual import work
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, ProgressBar, RichLog, Select
from textual.worker import get_current_worker

from config import LLM_PROVIDER, NUM_PASSES, OLLAMA_MODEL
from pipeline import (
    DoneEvent, ErrorEvent, LogLine, PipelineOptions, ProgressEvent, StageEvent,
    run_pipeline,
)
from tui.widgets.checkbox import Checkbox

_PROVIDERS = [("anthropic", "anthropic"), ("xiaomi", "xiaomi"), ("ollama", "ollama"), ("bonsai", "bonsai")]


class RunPane(Vertical):
    def compose(self):
        with Container(classes="panel", id="configure_panel"):
            with Horizontal(classes="run-form-row"):
                yield Label("Binary:")
                yield Input(placeholder="Path to binary...", id="binary_input")
            with Horizontal(classes="run-form-row"):
                yield Label("Provider:")
                yield Select(_PROVIDERS, value=LLM_PROVIDER, id="provider_select", allow_blank=False, compact=True)
                yield Label("Passes:")
                yield Input(value=str(NUM_PASSES), type="integer", id="passes_input")
                yield Label("Limit:")
                yield Input(value="0", type="integer", id="limit_input")
            with Horizontal(classes="run-form-row"):
                yield Checkbox("Restart (ignore cache)", id="restart_checkbox", compact=True)
                yield Checkbox("Verbose Ghidra output", id="verbose_checkbox", compact=True)
            with Horizontal(classes="run-form-row"):
                yield Button("Start", id="start_btn", variant="success", flat=True)
                yield Button("Stop", id="stop_btn", variant="error", disabled=True, flat=True)
        with Container(classes="panel", id="progress_panel"):
            yield ProgressBar(id="progress", show_eta=False)
            yield RichLog(id="log", wrap=True, max_lines=2000, auto_scroll=True, markup=True)

    def on_mount(self) -> None:
        self.query_one("#configure_panel", Container).border_title = "Configure"
        self.query_one("#progress_panel", Container).border_title = "Progress"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start_btn":
            self._start_run()
        elif event.button.id == "stop_btn":
            self.query_one("#log", RichLog).write("[yellow]Cancelling... (finishing current function)[/yellow]")
            self.query_one("#stop_btn", Button).disabled = True
            self.app.workers.cancel_group(self, "pipeline")

    def _read_form(self) -> PipelineOptions:
        passes_raw = self.query_one("#passes_input", Input).value.strip()
        limit_raw = self.query_one("#limit_input", Input).value.strip()
        return PipelineOptions(
            binary_path=self.query_one("#binary_input", Input).value.strip(),
            restart=self.query_one("#restart_checkbox", Checkbox).value,
            num_passes=int(passes_raw) if passes_raw else NUM_PASSES,
            limit=int(limit_raw) if limit_raw else 0,
            provider=self.query_one("#provider_select", Select).value,
            ollama_model=OLLAMA_MODEL,
            verbose=self.query_one("#verbose_checkbox", Checkbox).value,
        )

    def _start_run(self) -> None:
        options = self._read_form()
        log = self.query_one("#log", RichLog)
        if not options.binary_path:
            log.write("[bold red]Error:[/bold red] enter a binary path first.")
            return
        log.clear()
        self._set_running(True)
        self.query_one("#progress", ProgressBar).update(total=100, progress=0)
        self._run_worker(options)

    def _set_running(self, running: bool) -> None:
        self.query_one("#start_btn", Button).disabled = running
        self.query_one("#stop_btn", Button).disabled = not running

    @work(exclusive=True, thread=True, group="pipeline")
    def _run_worker(self, options: PipelineOptions) -> None:
        worker = get_current_worker()

        def on_event(event):
            self.app.call_from_thread(self._handle_event, event)

        try:
            run_pipeline(options, on_event=on_event, should_cancel=lambda: worker.is_cancelled)
        except Exception as e:
            self.app.call_from_thread(self._handle_event, ErrorEvent(str(e)))

    def _handle_event(self, event) -> None:
        log = self.query_one("#log", RichLog)
        if isinstance(event, StageEvent):
            style = "yellow" if event.level == "warn" else ""
            log.write(f"[{style}]{event.message}[/{style}]" if style else event.message)
        elif isinstance(event, LogLine):
            log.write(event.text)
        elif isinstance(event, ProgressEvent):
            progress = self.query_one("#progress", ProgressBar)
            progress.update(total=event.total, progress=event.completed)
            suffix = " [dim](cached)[/dim]" if event.cached else ""
            log.write(f"[{event.completed}/{event.total}] {event.function_name}{suffix}")
        elif isinstance(event, DoneEvent):
            if event.cancelled:
                log.write(f"[bold yellow]Cancelled.[/bold yellow] Partial output: {event.output_path}")
            else:
                log.write(f"[bold green]Done.[/bold green] Output: {event.output_path}")
            self._set_running(False)
        elif isinstance(event, ErrorEvent):
            log.write(f"[bold red]ERROR:[/bold red] {event.message}")
            self._set_running(False)
