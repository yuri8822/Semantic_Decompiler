"""Memory tab: read-only browser over semantic.db (functions, recovered
types, variable names, call graph) — for inspecting what the multi-pass
translator has inferred, e.g. while debugging ai/translator.py."""

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Select, Static

from tui import memory_data
from tui.memory_data import BINARY_FIELD, DETAIL_FIELD, DISPLAY_COLUMNS, TABLE_LABELS


class MemoryPane(Vertical):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_table = memory_data.FUNCTIONS
        self._all_rows: list[dict] = []
        self._rows_by_key: dict[str, dict] = {}
        self._sort_key = None
        self._sort_reverse = False

    def compose(self):
        with Container(classes="panel", id="memory_filter_panel"):
            with Horizontal(id="memory_filter_row"):
                yield Select(
                    [(label, key) for key, label in TABLE_LABELS.items()],
                    value=self._current_table, allow_blank=False, id="memory_table_select",
                )
                yield Input(placeholder="Filter by binary...", id="memory_binary_input")
                yield Input(placeholder="Search...", id="memory_search_input")
                yield Button("Refresh", id="memory_refresh_btn", flat=True)
        with Horizontal(id="memory_body"):
            with Container(classes="panel", id="memory_table_panel"):
                yield DataTable(id="memory_table", cursor_type="row")
            with VerticalScroll(classes="panel", id="memory_detail_panel"):
                yield Static(id="memory_detail")

    def on_mount(self) -> None:
        self.query_one("#memory_filter_panel", Container).border_title = "Browse"
        self.query_one("#memory_table_panel", Container).border_title = "Rows"
        self.query_one("#memory_detail_panel", VerticalScroll).border_title = "Detail"
        self._set_columns()
        self.query_one("#memory_binary_input", Input).disabled = (
            BINARY_FIELD[self._current_table] is None
        )
        self._show_placeholder()
        self.refresh_data()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh_data(self) -> None:
        """Reload the current table's rows from semantic.db and re-render."""
        self._all_rows = memory_data.load_rows(self._current_table)
        self._render_rows()

    def _set_columns(self) -> None:
        table = self.query_one("#memory_table", DataTable)
        table.clear(columns=True)
        for header, field in DISPLAY_COLUMNS[self._current_table]:
            table.add_column(header, key=field)

    def _render_rows(self) -> None:
        table = self.query_one("#memory_table", DataTable)
        table.clear()
        self._rows_by_key = {}

        binary_field = BINARY_FIELD[self._current_table]
        binary_filter = self.query_one("#memory_binary_input", Input).value.strip().lower()
        search_filter = self.query_one("#memory_search_input", Input).value.strip().lower()

        for row in self._all_rows:
            if binary_field and binary_filter:
                if binary_filter not in str(row.get(binary_field, "")).lower():
                    continue
            if search_filter:
                if not any(search_filter in str(v).lower() for v in row.values()):
                    continue
            key = memory_data.row_key(self._current_table, row)
            self._rows_by_key[key] = row
            cells = [str(row.get(field, "")) for _, field in DISPLAY_COLUMNS[self._current_table]]
            table.add_row(*cells, key=key)

        if self._sort_key is not None:
            table.sort(self._sort_key, reverse=self._sort_reverse)

    def _show_placeholder(self) -> None:
        self.query_one("#memory_detail", Static).update("[dim]Select a row to see details.[/dim]")

    def _show_detail(self, row: dict) -> None:
        field, label, lexer = DETAIL_FIELD[self._current_table]

        meta = Text()
        for key, value in row.items():
            if key in ("id", field):
                continue
            meta.append(f"{key}: ", style="bold")
            meta.append(f"{value}\n")

        parts = [meta]
        if field and row.get(field):
            parts.append(Text(f"\n{label}:", style="bold"))
            if lexer:
                parts.append(Syntax(
                    row[field], lexer, theme="monokai",
                    background_color="#0a0810", word_wrap=True,
                ))
            else:
                parts.append(Text(row[field]))
        self.query_one("#memory_detail", Static).update(Group(*parts))

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "memory_table_select":
            return
        self._current_table = event.value
        self._sort_key = None
        self._sort_reverse = False
        self._set_columns()
        self.query_one("#memory_binary_input", Input).disabled = (
            BINARY_FIELD[self._current_table] is None
        )
        self._show_placeholder()
        self.refresh_data()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("memory_binary_input", "memory_search_input"):
            self._render_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "memory_refresh_btn":
            self.refresh_data()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.data_table is not self.query_one("#memory_table", DataTable):
            return
        if self._sort_key == event.column_key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = event.column_key
            self._sort_reverse = False
        event.data_table.sort(self._sort_key, reverse=self._sort_reverse)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table is not self.query_one("#memory_table", DataTable):
            return
        row = self._rows_by_key.get(event.row_key.value)
        if row is not None:
            self._show_detail(row)
