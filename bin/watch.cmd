@echo off
set "AI_MEMORY_PY=%~dp0python.exe"
if exist "%AI_MEMORY_PY%" (
  "%AI_MEMORY_PY%" -m ai_memory_cli watch %*
) else (
  python -m ai_memory_cli watch %*
)
