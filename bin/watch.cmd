@echo off
set "MEMVORA_PY=%~dp0python.exe"
if exist "%MEMVORA_PY%" (
  "%MEMVORA_PY%" -m memvora_cli watch %*
) else (
  python -m memvora_cli watch %*
)
