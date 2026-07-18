"""Bottom keybinding hint bar: a `<key> <label>` strip."""

from rich.text import Text
from textual.widgets import Static

_KEY_COLOR = "#b9a7e6"
_LABEL_COLOR = "#6b6577"


class HintBar(Static):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pairs: list[tuple[str, str]] = []

    def set_hints(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs
        self.refresh()

    def render(self) -> Text:
        out = Text()
        for i, (key, label) in enumerate(self._pairs):
            if i:
                out.append("   ")
            out.append(key, style=f"bold {_KEY_COLOR}")
            out.append(" ")
            out.append(label, style=_LABEL_COLOR)
        return out
