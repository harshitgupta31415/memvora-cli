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
watch
```

The `auth` command verifies the website-issued token with FastAPI before it is saved locally. After verification,
the CLI stores a SHA-512 user hash for this computer, binds the token to that hash on the server, and starts the
background sync agent once on Windows.

If you run `watch` before auth, it will prompt for the website CLI token and FastAPI URL, then continue into
terminal capture after verification.

`watch` is a shortcut for `python -m ai_memory_cli watch`. If Windows Device Guard blocks the generated launcher, keep using `python -m ai_memory_cli watch`.
On Windows the shortcut is installed as `watch.cmd`; the Python Scripts folder must be on `PATH` for bare `watch` to resolve.

Use `python -m ai_memory_cli run -- COMMAND` when you only want to record one command.

On Windows, `python -m ai_memory_cli ...` is the safest form because it avoids PATH issues and Device Guard policies that can block pip's generated `ai-memory.exe` launcher. Also avoid angle bracket placeholders in CMD because they are treated as file redirection.

Inside `watch`, type the real command you want to capture, for example `python --version`. Do not type `python -m ai_memory_cli run -- ...` inside `watch`, or you will capture the nested CLI command too.
Use `cls` on Windows or `clear` on Unix shells to clear the watch screen; those control commands are not stored or synced.
On Windows, commands run through PowerShell, so type `ls` directly. Do not type `powershell` or `cmd` inside `watch`; nested shell launchers are ignored and not stored.
The watch prompt shows the active folder, for example `ai-memory C:\work\repo>`. Use `cd`, `chdir`, `cd..`, `cd /d D:\path`, `cd ~`, or `cd -` normally; successful directory changes are tracked as hashed terminal events and become the working folder for the next command.

## Background agent

After `auth`, the background agent starts once and is installed in the Windows Startup folder so queued terminal
hashes keep syncing whenever the API is reachable. Use these commands when you need manual control:

```powershell
python -m ai_memory_cli agent status
python -m ai_memory_cli agent stop
python -m ai_memory_cli agent start
```

The agent does not secretly capture every terminal on the computer. Commands are captured when they run through:

```powershell
python -m ai_memory_cli watch
python -m ai_memory_cli run -- python --version
```

To remove the startup task:

```powershell
python -m ai_memory_cli agent stop
python -m ai_memory_cli agent uninstall
```

Agent logs are written to `%USERPROFILE%\.ai-memory-cli\logs\agent.log`.

## Storage

The CLI stores config and unsynced events in a separate folder:

- Windows: `%USERPROFILE%\.ai-memory-cli`
- macOS/Linux: `~/.ai-memory-cli`

Set `AI_MEMORY_CLI_HOME` to override this location.

Accepted command observations are also written as plain daily hash logs:

- Windows: `%USERPROFILE%\.ai-memory-cli\history\YYYY-MM-DD.log`
- macOS/Linux: `~/.ai-memory-cli/history/YYYY-MM-DD.log`

These files include time/date, event hash, command hash, output hash, source, exit code, and working-folder name.

The readable command/output mapping is stored locally in:

- Windows: `%USERPROFILE%\.ai-memory-cli\dictionary\terminal-dictionary.json`
- macOS/Linux: `~/.ai-memory-cli/dictionary/terminal-dictionary.json`

When synced, FastAPI also stores that mapping in:

```text
local-storage/cli/<github-account>/<user-hash-prefix>/terminal-dictionary.json
```

Use this dictionary to map `command_hash`, `output_hash`, or `event_hash` back to the command text and captured output.

If a command is clearly invalid, such as a pasted prompt (`ai-memory> python --version`) or a shell "not recognized" error, the CLI skips storing it as an event.

## Storage, meaning, and dedupe

The CLI stores two related records:

- hash event files in `events/`, `outbox/`, `sent/`, and backend `terminal-events/`
- readable mapping in `terminal-dictionary.json`

The dictionary contains:

- `commands[command_hash].command`
- `outputs[output_hash].stdout`
- `outputs[output_hash].stderr`
- `events[event_hash].command`
- `events[event_hash].stdout`
- `events[event_hash].stderr`

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
