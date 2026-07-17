@echo off
REM Starts the Bonsai 27B (1-bit) llama.cpp server for the decompiler pipeline,
REM in its own console window (via `start`) so it keeps running independently
REM of whatever launched this script.
REM Listens on http://localhost:8080 (OpenAI-compatible API + built-in chat UI).
REM %~dp0 = this script's own directory, so it works no matter where it's run from.
REM
REM Deliberately NOT passing --reasoning-budget here: that flag caps thinking
REM server-wide for every request, which would override the pipeline's
REM per-pass "thinking_budget_tokens" (see bonsai_provider.py) — llama.cpp
REM only honors that per-request field when the server-side flag is unset.
REM Manual chats through the web UI still get unbounded thinking by default
REM this way; add --reasoning-budget N back here if you want THAT bounded too.

start "Bonsai 27B Server" /D "%~dp0ai\providers\bonsai\llama.cpp\bin\extracted" cmd /k llama-server.exe -m ..\..\..\model\Bonsai-27B-Q1_0.gguf --host 0.0.0.0 --port 8080 -ngl 99
