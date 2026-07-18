"""Launcher for the Textual TUI: `python tui_main.py`."""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from tui.app import DecompilerApp

if __name__ == "__main__":
    DecompilerApp().run()
