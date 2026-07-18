"""Dark violet color theme: near-black purple background, violet accents,
muted secondary text."""

from textual.theme import Theme

DECOMPILER_THEME = Theme(
    name="decompiler-dark",
    primary="#a78bfa",
    secondary="#b9a7e6",
    accent="#a78bfa",
    success="#86d6a2",
    warning="#f0c560",
    error="#e5484d",
    foreground="#ddd8ea",
    background="#0a0810",
    surface="#120f1a",
    panel="#17131f",
    dark=True,
    variables={
        "text-muted": "#6b6577",
        "title-muted": "#8a8a8a",
        "border-muted": "#2d2b31",
    },
)
