"""Settings tab: edit config.py's tunables (Ghidra path, provider/model
choices, timeouts) from the TUI instead of hand-editing the file. Writes to
settings.json, which config.py loads as an override layer at import time —
so changes take effect on the next app start, not the current one."""

from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Static

from tui import settings_data
from tui.settings_data import FIELDS, SECTIONS


def _section_id(section: str) -> str:
    return "settings_section_" + section.lower().replace(" ", "_")


def _input_id(key: str) -> str:
    return f"setting_{key}"


class SettingsPane(VerticalScroll):
    def compose(self):
        for section in SECTIONS:
            with Container(classes="panel settings-section", id=_section_id(section)):
                for field in FIELDS:
                    if field.section != section:
                        continue
                    with Horizontal(classes="run-form-row"):
                        yield Label(f"{field.label}:")
                        yield Input(id=_input_id(field.key), classes="settings-input")
        with Horizontal(id="settings_actions"):
            yield Button("Save", id="settings_save_btn", variant="success", flat=True)
            yield Button("Reset to Defaults", id="settings_reset_btn", variant="error", flat=True)
        yield Static(id="settings_status")

    def on_mount(self) -> None:
        for section in SECTIONS:
            self.query_one(f"#{_section_id(section)}", Container).border_title = section
        self._load_values(settings_data.load_current_values())
        self._set_status(
            f"[dim]Settings are stored in {settings_data.SETTINGS_PATH} — "
            f"changes take effect after restarting the app.[/dim]"
        )

    def _load_values(self, values: dict) -> None:
        for field in FIELDS:
            self.query_one(f"#{_input_id(field.key)}", Input).value = str(values[field.key])

    def _set_status(self, text: str) -> None:
        self.query_one("#settings_status", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings_save_btn":
            self._save()
        elif event.button.id == "settings_reset_btn":
            self._reset()

    def _save(self) -> None:
        values = {}
        for field in FIELDS:
            raw = self.query_one(f"#{_input_id(field.key)}", Input).value.strip()
            if field.type == "int":
                try:
                    values[field.key] = int(raw)
                except ValueError:
                    self._set_status(f"[bold red]Error:[/bold red] \"{field.label}\" must be a whole number.")
                    return
            else:
                values[field.key] = raw
        settings_data.save_overrides(values)
        self._set_status(
            "[bold green]Saved.[/bold green] [dim]Restart the app for changes to take effect.[/dim]"
        )

    def _reset(self) -> None:
        settings_data.reset_to_defaults()
        self._load_values(settings_data.load_hardcoded_defaults())
        self._set_status(
            "[bold yellow]Reset to defaults.[/bold yellow] [dim]Restart the app for this to take effect.[/dim]"
        )
