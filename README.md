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
ai-memory auth --token <app-issued-cli-token> --api-url https://api.your-domain.com
ai-memory init --project my-project --repo owner/repo --workspace .
ai-memory workspace connect --path . --repo owner/repo --editor vscode --package-manager pip
ai-memory watch
```

Use `ai-memory run -- <command>` when you only want to record one command.

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
