"""A Checkbox with a checkmark glyph instead of the default "X"."""

from textual.widgets import Checkbox as _Checkbox


class Checkbox(_Checkbox):
    BUTTON_INNER = "✓"
