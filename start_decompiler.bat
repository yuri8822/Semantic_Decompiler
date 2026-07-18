@echo off
REM Launches the Textual TUI (tui_main.py) in its own console window (via
REM `start`) so it keeps running independently of whatever launched this
REM script, and `cmd /k` keeps that window open after exit so a crash/error
REM stays visible instead of flashing shut.
REM %~dp0 = this script's own directory, so it works no matter where it's run from.

start "AI Semantic Decompiler" /D "%~dp0" cmd /k python tui_main.py
