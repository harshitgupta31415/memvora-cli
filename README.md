# AI Memory CLI

Standalone Python CLI for terminal capture, hashing, offline queueing, and sync to the temporary FastAPI backend.

## Install

For any user machine after the package is published:

```powershell
python -m pip install ai-memory-cli
```

Before PyPI publish, install from GitHub:

```powershell
python -m pip install "ai-memory-cli @ git+https://github.com/YOUR_ORG/ai-memory-cli.git"
```

For local development from this CLI repo:

```powershell
python -m pip install -e .
```

## Basic flow

```powershell
python -m ai_memory_cli auth --token TOKEN_FROM_WEBSITE --api-url https://api.your-domain.com
python -m ai_memory_cli init --project my-project --repo owner/repo --workspace .
python -m ai_memory_cli workspace connect --path . --repo owner/repo --editor vscode --package-manager pip
python -m ai_memory_cli watch
```

Use `python -m ai_memory_cli run -- COMMAND` when you only want to record one command.

On Windows, `python -m ai_memory_cli ...` is the safest form because it avoids PATH issues and Device Guard policies that can block pip's generated `ai-memory.exe` launcher. Also avoid angle bracket placeholders in CMD because they are treated as file redirection.

Inside `watch`, type the real command you want to capture, for example `python --version`. Do not type `python -m ai_memory_cli run -- ...` inside `watch`, or you will capture the nested CLI command too.

## Storage

The CLI stores config and unsynced events in a separate folder:

- Windows: `%USERPROFILE%\.ai-memory-cli`
- macOS/Linux: `~/.ai-memory-cli`

Set `AI_MEMORY_CLI_HOME` to override this location.

## Privacy and dedupe

The CLI does not send raw commands or raw output to the backend. It sends:

- `command_hash`
- `output_hash`
- `event_hash`
- timestamps, exit code, shell, project, repo, and local metadata

If the same command produces the same output again, the CLI keeps one event hash and increments `duplicate_count`.

## Excluded commands

Long-running development commands are not captured by default. They still run, but no hash event is stored.

Default excluded patterns include:

- `npm run ...`
- `next dev`
- `vite`
- `uvicorn --reload`
- `python -m uvicorn ... --reload`

Use `--include-excluded` on `run` or `watch` if you need to capture them anyway.

## Publish

After this folder is pushed as its own public GitHub repo, publish to PyPI with:

```powershell
.\scripts\publish.ps1 -Repository pypi
```
