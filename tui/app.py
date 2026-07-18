"""Textual TUI shell — Run tab drives the pipeline, Results tab browses output."""

from textual.app import App, ComposeResult
from textual.widgets import TabbedContent, TabPane

from tui.theme import DECOMPILER_THEME
from tui.widgets.gradient_title import GradientTitle
from tui.widgets.hint_bar import HintBar
from tui.widgets.results_pane import ResultsPane
from tui.widgets.run_pane import RunPane

_HINTS = {
    "run": [
        ("tab", "Focus next"),
        ("shift+tab", "Focus previous"),
        ("enter", "Activate"),
        ("q", "Quit"),
    ],
    "results": [
        ("↑↓", "Move"),
        ("enter", "Select"),
        ("tab", "Focus next"),
        ("q", "Quit"),
    ],
}


class DecompilerApp(App):
    CSS_PATH = "app.tcss"
    BINDINGS = [("q", "quit", "Quit")]
    TITLE = "AI Semantic Decompiler"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.register_theme(DECOMPILER_THEME)
        self.theme = "decompiler-dark"

    def compose(self) -> ComposeResult:
        yield GradientTitle("AI SEMANTIC DECOMPILER")
        with TabbedContent(initial="run"):
            with TabPane("Run", id="run"):
                yield RunPane()
            with TabPane("Results", id="results"):
                yield ResultsPane()
        yield HintBar()

    def on_mount(self) -> None:
        self.query_one(HintBar).set_hints(_HINTS["run"])

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        hints = _HINTS.get(event.tabbed_content.active)
        if hints is not None:
            self.query_one(HintBar).set_hints(hints)
