"""Results tab: browse a binary's functions, view Ghidra-C vs recovered-C++."""

from rich.syntax import Syntax
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Static

from tui.results_data import load_results


class ResultsPane(Vertical):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rows_by_address: dict[str, object] = {}

    def compose(self):
        with Container(classes="panel", id="load_panel"):
            with Horizontal(id="results_load_row"):
                yield Input(placeholder="Path to binary...", id="results_binary_input")
                yield Button("Load", id="load_btn", flat=True)
        with Horizontal(id="results_body"):
            with Container(classes="panel", id="table_panel"):
                yield DataTable(id="fn_table", cursor_type="row")
            with Horizontal(id="code_panes"):
                with VerticalScroll(classes="panel code_pane", id="before_panel"):
                    yield Static(id="before_code")
                with VerticalScroll(classes="panel code_pane", id="after_panel"):
                    yield Static(id="after_code")

    def on_mount(self) -> None:
        self.query_one("#load_panel", Container).border_title = "Load Binary"
        self.query_one("#table_panel", Container).border_title = "Functions"
        self.query_one("#before_panel", VerticalScroll).border_title = "Ghidra (before)"
        self.query_one("#after_panel", VerticalScroll).border_title = "AI Recovered (after)"

        table = self.query_one("#fn_table", DataTable)
        table.add_columns("Address", "Ghidra Name", "AI Name", "Status")
        self.query_one("#before_code", Static).update("[dim]Load a binary and pick a function.[/dim]")
        self.query_one("#after_code", Static).update("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load_btn":
            self._load()

    def _load(self) -> None:
        binary_path = self.query_one("#results_binary_input", Input).value.strip()
        table = self.query_one("#fn_table", DataTable)
        before = self.query_one("#before_code", Static)
        if not binary_path:
            before.update("[bold red]Error:[/bold red] enter a binary path first.")
            return
        try:
            rows = load_results(binary_path)
        except FileNotFoundError as e:
            before.update(f"[bold red]Error:[/bold red] {e}")
            return

        table.clear()
        self._rows_by_address = {row.address: row for row in rows}
        for row in rows:
            status = "translated" if row.translated else "pending"
            table.add_row(row.address, row.ghidra_name, row.ai_name or "-", status, key=row.address)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self._rows_by_address.get(event.row_key.value)
        if row is None:
            return
        before = self.query_one("#before_code", Static)
        after = self.query_one("#after_code", Static)
        before.update(Syntax(row.decompiled or "// (empty)", "c", theme="monokai",
                             background_color="#0a0810", line_numbers=True, word_wrap=True))
        after.update(Syntax(row.final_cpp, "cpp", theme="monokai",
                            background_color="#0a0810", line_numbers=True, word_wrap=True))
