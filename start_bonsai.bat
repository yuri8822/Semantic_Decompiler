@echo off
REM Starts the Bonsai 27B (1-bit) llama.cpp server for the decompiler pipeline.
REM Listens on http://localhost:8080 (OpenAI-compatible API + built-in chat UI).
REM %~dp0 = this script's own directory, so it works no matter where it's run from.

cd /d "%~dp0ai\providers\bonsai\llama.cpp\bin\extracted"
llama-server.exe -m ..\..\..\model\Bonsai-27B-Q1_0.gguf --host 0.0.0.0 --port 8080 -ngl 99
