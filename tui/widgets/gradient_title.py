"""A single-line title with a left-to-right color gradient wordmark."""

from rich.text import Text
from textual.widgets import Static

_START = (0xF5, 0xEC, 0xFF)  # light lavender
_END = (0xA7, 0x8B, 0xFA)    # accent violet


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    r = round(c1[0] + (c2[0] - c1[0]) * t)
    g = round(c1[1] + (c2[1] - c1[1]) * t)
    b = round(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


class GradientTitle(Static):
    def __init__(self, text: str, **kwargs):
        super().__init__(**kwargs)
        self._text = text

    def render(self) -> Text:
        out = Text()
        n = max(len(self._text) - 1, 1)
        for i, ch in enumerate(self._text):
            color = _lerp_color(_START, _END, i / n)
            out.append(ch, style=f"bold {color}")
        return out
