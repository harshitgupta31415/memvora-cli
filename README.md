# Memvora CLI

Standalone Python CLI for terminal capture, hashing, offline queueing, and sync to the temporary FastAPI backend.

## Install

For any user machine after the package is published:

```powershell
python -m pip install memvora
```

Before PyPI publish, install from GitHub:

```powershell
python -m pip install "memvora @ git+https://github.com/harshitgupta31415/memvora-cli.git"
```

For local development from this CLI repo:

```powershell
python -m pip install -e .
```

## Basic flow

```powershell
python -m memvora_cli auth --token TOKEN_FROM_WEBSITE --api-url https://memvora.onrender.com/api
python -m memvora_cli init --project my-project --repo owner/repo --workspace .
python -m memvora_cli workspace connect --path . --repo owner/repo --package-manager pip
watch "backend setup"
```

The `auth` command verifies the website-issued token with FastAPI before it is saved locally. After verification,
the CLI stores a SHA-512 user hash for this computer, binds the token to that hash on the server, and starts the
background sync agent once on Windows.

If you run `watch` before auth, it will prompt for the website CLI token and Memvora API URL, defaulting to
`https://memvora.onrender.com/api` when you press Enter, then continue into terminal capture after verification.

`watch` is a shortcut for `python -m memvora_cli watch`. Give it a name, such as `watch "backend setup"`, so every command until `exit` is grouped under that work session. If Windows Device Guard blocks the generated launcher, keep using `python -m memvora_cli watch "backend setup"`.
On Windows the shortcut is installed as `watch.cmd`; the Python Scripts folder must be on `PATH` for bare `watch` to resolve.

Use `watch --logout` when you want this computer to forget its saved CLI token and website session before starting capture again. It clears the auth fields in `%USERPROFILE%\.memvora\config.json`, keeps your local history/queue files, then asks for a fresh website CLI token.

Use `python -m memvora_cli run -- COMMAND` when you only want to record one command.

On Windows, `python -m memvora_cli ...` is the safest form because it avoids PATH issues and Device Guard policies that can block pip's generated `memvora.exe` launcher. Also avoid angle bracket placeholders in CMD because they are treated as file redirection.

Inside `watch`, type the real command you want to capture, for example `python --version`. Do not type `python -m memvora_cli run -- ...` inside `watch`, or you will capture the nested CLI command too.
Use `cls` on Windows or `clear` on Unix shells to clear the watch screen; those control commands are not stored or synced.
On Windows, commands run through PowerShell, so type `ls` directly. Do not type `powershell` or `cmd` inside `watch`; nested shell launchers are ignored and not stored.
The watch prompt shows the session name and active folder, for example `memvora[backend setup] C:\work\repo>`. Use `cd`, `chdir`, `cd..`, `cd /d D:\path`, `cd ~`, or `cd -` normally; successful directory changes are tracked as hashed terminal events and become the working folder for the next command.

## Background agent

After `auth`, the background agent starts once and is installed in the Windows Startup folder so queued terminal
hashes keep syncing whenever the API is reachable. Command capture itself does not wait for network sync; it stores
locally first and lets the agent upload in the background. Use these commands when you need manual control:

```powershell
python -m memvora_cli agent status
python -m memvora_cli agent stop
python -m memvora_cli agent start
```

The agent does not secretly capture every terminal on the computer. Commands are captured when they run through:

```powershell
python -m memvora_cli watch "backend setup"
python -m memvora_cli run -- python --version
```

To remove the startup task:

```powershell
python -m memvora_cli agent stop
python -m memvora_cli agent uninstall
```

Agent logs are written to `%USERPROFILE%\.memvora\logs\agent.log`.

## Storage

The CLI stores config and unsynced events in a separate folder:

- Windows: `%USERPROFILE%\.memvora`
- macOS/Linux: `~/.memvora`

Set `MEMVORA_CLI_HOME` to override this location.

Accepted command observations are also written as plain daily hash logs:

- Windows: `%USERPROFILE%\.memvora\history\YYYY-MM-DD.log`
- macOS/Linux: `~/.memvora/history/YYYY-MM-DD.log`

These files include time/date, event hash, command hash, output hash, source, exit code, and working-folder name.

The readable command/output mapping is stored locally in:

- Windows: `%USERPROFILE%\.memvora\dictionary\terminal-dictionary.json`
- macOS/Linux: `~/.memvora/dictionary/terminal-dictionary.json`

When synced, FastAPI also stores that mapping in PostgreSQL under:

```text
cli/<github-account>/<user-hash-prefix>/terminal-dictionary.json
```

Use this dictionary to map `command_hash`, `output_hash`, or `event_hash` back to the command text and captured output.

Each named watch session is also stored as one readable JSON file:

- Windows: `%USERPROFILE%\.memvora\dictionary\watch-sessions\<watch-id>.json`
- macOS/Linux: `~/.memvora/dictionary/watch-sessions/<watch-id>.json`

When synced, FastAPI stores the same named session in PostgreSQL under:

```text
cli/<github-account>/<user-hash-prefix>/terminal-watch-sessions/<watch-id>.json
```

If a command is clearly invalid, such as a pasted prompt (`memvora> python --version`) or a shell "not recognized" error, the CLI skips storing it as an event.

## Storage, meaning, and dedupe

The CLI stores three related records:

- readable event files in `events/`, `outbox/`, `sent/`, and backend `terminal-events/`
- readable mapping in `terminal-dictionary.json`
- readable named watch sessions in `dictionary/watch-sessions/`

Event files contain readable fields such as `command`, `stdout`, `stderr`, `output`, and `cwd`, plus `command_hash`, `output_hash`, and `event_hash` for dedupe.

The dictionary contains:

- `commands[command_hash].command`
- `outputs[output_hash].stdout`
- `outputs[output_hash].stderr`
- `events[event_hash].command`
- `events[event_hash].stdout`
- `events[event_hash].stderr`
- `watch_sessions[watch_id].watch_name`
- `watch_sessions[watch_id].event_hashes`

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
