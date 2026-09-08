@echo off
REM Starts a local llama.cpp server for the decompiler pipeline, in its own
REM console window (via `start`) so it keeps running independently of whatever
REM launched this script.
REM Listens on http://localhost:8080 (OpenAI-compatible API + built-in chat UI),
REM which is what config.py's LLAMACPP_BASE_URL points at.
REM
REM Expects llama-server to be a system install resolvable on PATH, and MODEL
REM to be the full path to whichever GGUF you want served. Neither the engine
REM nor any weights live in this repo.

set MODEL=

if "%MODEL%"=="" (
  echo [start_llamacpp] Set MODEL in this script to the full path of a .gguf file first.
  exit /b 1
)

where llama-server >nul 2>nul || (
  echo [start_llamacpp] llama-server not found on PATH -- add your llama.cpp install to PATH,
  echo                  or replace `llama-server` below with its full path.
  exit /b 1
)

start "llama.cpp Server" cmd /k llama-server --model "%MODEL%" --host 0.0.0.0 --port 8080 -ngl 99
