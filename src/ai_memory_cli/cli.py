from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_EXCLUDES = [
    r"^\s*npm\s+run(\s|$)",
    r"^\s*npm\s+start(\s|$)",
    r"^\s*pnpm\s+(dev|start)(\s|$)",
    r"^\s*yarn\s+(dev|start)(\s|$)",
    r"^\s*bun\s+(dev|start)(\s|$)",
    r"^\s*next\s+dev(\s|$)",
    r"^\s* vite(\s|$)",
    r"^\s*vite(\s|$)",
    r"uvicorn\b.*\s--reload(\s|$)",
    r"python(\.exe)?\s+-m\s+uvicorn\b.*\s--reload(\s|$)",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def cli_home() -> Path:
    configured = os.getenv("AI_MEMORY_CLI_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".ai-memory-cli").resolve()


def ensure_dirs(home: Path) -> None:
    for folder in ["events", "outbox", "sent", "logs"]:
        (home / folder).mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def config_path(home: Path) -> Path:
    return home / "config.json"


def load_config(home: Path) -> dict[str, Any]:
    ensure_dirs(home)
    config = read_json(config_path(home), {})
    config.setdefault("api_url", DEFAULT_API_URL)
    config.setdefault("project", "")
    config.setdefault("repository", "")
    config.setdefault("workspace_path", ".")
    config.setdefault("exclude_patterns", DEFAULT_EXCLUDES)
    return config


def save_config(home: Path, config: dict[str, Any]) -> None:
    ensure_dirs(home)
    write_json(config_path(home), config)


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def normalize_output(stdout: str, stderr: str) -> str:
    combined = f"stdout:\n{stdout}\nstderr:\n{stderr}"
    lines = [line.rstrip() for line in combined.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def selected_shell() -> str:
    shell = os.getenv("SHELL") or os.getenv("COMSPEC") or ""
    if shell:
        return Path(shell).name
    return "powershell" if os.name == "nt" else "sh"


def command_line(parts: list[str]) -> str:
    cleaned = list(parts)
    if cleaned and cleaned[0] == "--":
        cleaned = cleaned[1:]
    if os.name == "nt":
        return subprocess.list2cmdline(cleaned)
    import shlex

    return shlex.join(cleaned)


def is_excluded(command: str, config: dict[str, Any]) -> bool:
    patterns = config.get("exclude_patterns") or DEFAULT_EXCLUDES
    return any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in patterns)


def api_url(config: dict[str, Any]) -> str:
    return str(config.get("api_url") or DEFAULT_API_URL).rstrip("/")


def require_token(config: dict[str, Any]) -> str:
    token = str(config.get("token") or "").strip()
    if not token:
        raise SystemExit("Run python -m ai_memory_cli auth --token TOKEN_FROM_WEBSITE first.")
    return token


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    token: str | None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "User-Agent": f"ai-memory-cli/{__version__}",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
    if not response_body:
        return {}
    return json.loads(response_body)


def make_terminal_event(
    command: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    started_at: str,
    ended_at: str,
    duration_ms: int,
    cwd: Path,
    config: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    normalized_command = normalize_command(command)
    normalized_output = normalize_output(stdout, stderr)
    command_hash = sha256_text(normalized_command)
    output_hash = sha256_text(normalized_output)
    event_hash = sha256_text(f"v1\0{normalized_command}\0{normalized_output}\0{exit_code}")
    cwd_text = str(cwd.resolve())
    return {
        "event_hash": event_hash,
        "command_hash": command_hash,
        "output_hash": output_hash,
        "started_at": started_at,
        "ended_at": ended_at,
        "observed_at": ended_at,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "cwd_hash": sha256_text(cwd_text),
        "shell": selected_shell(),
        "project": str(config.get("project") or ""),
        "repository": str(config.get("repository") or ""),
        "source": source,
        "duplicate_count": 1,
        "metadata": {
            "platform": platform.system(),
            "python": platform.python_version(),
            "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            "command_length": len(command),
            "cwd_tail": cwd.resolve().name,
        },
    }


def store_event(home: Path, event: dict[str, Any]) -> tuple[bool, Path]:
    ensure_dirs(home)
    event_hash = event["event_hash"]
    event_path = home / "events" / f"{event_hash}.json"
    outbox_path = home / "outbox" / f"{event_hash}.json"
    existing = read_json(event_path, None)
    if existing:
        existing["total_observed_count"] = int(existing.get("total_observed_count", 1)) + 1
        existing["last_observed_at"] = event["observed_at"]
        write_json(event_path, existing)

        outbound = read_json(outbox_path, None)
        if outbound:
            outbound["duplicate_count"] = int(outbound.get("duplicate_count", 1)) + 1
            outbound["observed_at"] = event["observed_at"]
        else:
            outbound = dict(event)
            outbound["duplicate_count"] = 1
        write_json(outbox_path, outbound)
        return False, event_path

    stored = dict(event)
    stored["total_observed_count"] = 1
    stored["first_observed_at"] = event["observed_at"]
    stored["last_observed_at"] = event["observed_at"]
    write_json(event_path, stored)
    write_json(outbox_path, event)
    return True, event_path


def mark_synced(home: Path, event_hash: str, response: dict[str, Any]) -> None:
    outbox_path = home / "outbox" / f"{event_hash}.json"
    event_path = home / "events" / f"{event_hash}.json"
    sent_path = home / "sent" / f"{event_hash}.json"
    outbox_event = read_json(outbox_path, {})
    stored_event = read_json(event_path, {})
    now = utc_now()
    if stored_event:
        stored_event["last_synced_at"] = now
        stored_event["synced_observed_count"] = stored_event.get("total_observed_count", 1)
        write_json(event_path, stored_event)
    write_json(
        sent_path,
        {
            "event_hash": event_hash,
            "synced_at": now,
            "duplicate_count": outbox_event.get("duplicate_count", 1),
            "response": response,
        },
    )
    if outbox_path.exists():
        outbox_path.unlink()


def sync_events(home: Path, config: dict[str, Any], limit: int = 50, quiet: bool = False) -> int:
    ensure_dirs(home)
    token = str(config.get("token") or "").strip()
    if not token:
        if not quiet:
            print("No CLI token saved. Events remain queued until python -m ai_memory_cli auth is configured.")
        return 0
    paths = sorted((home / "outbox").glob("*.json"))[:limit]
    if not paths:
        if not quiet:
            print("No queued terminal events to sync.")
        return 0

    events = [read_json(path, {}) for path in paths]
    events = [event for event in events if event.get("event_hash")]
    if not events:
        return 0

    payload = {
        "events": events,
        "client": {
            "name": "ai-memory-cli",
            "version": __version__,
            "storage_home_hash": sha256_text(str(home)),
            "hostname_hash": sha256_text(platform.node() or "unknown"),
        },
    }
    response = http_json("POST", f"{api_url(config)}/cli/events/terminal", payload, token)
    accepted = {item.get("event_hash") for item in response.get("events", []) if item.get("event_hash")}
    for event in events:
        if event["event_hash"] in accepted:
            mark_synced(home, event["event_hash"], response)

    synced = len(accepted)
    if not quiet:
        print(f"Synced {synced} terminal event(s).")
    return synced


def capture_command(home: Path, config: dict[str, Any], command: str, include_excluded: bool, source: str) -> int:
    workspace = Path(str(config.get("workspace_path") or ".")).expanduser()
    cwd = workspace if workspace.exists() else Path.cwd()

    if is_excluded(command, config) and not include_excluded:
        print(f"ai-memory: running without capture because this command is excluded: {command}", file=sys.stderr)
        return subprocess.call(command, shell=True, cwd=str(cwd))

    started_at = utc_now()
    started_monotonic = time.monotonic()
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ended_at = utc_now()
    duration_ms = int((time.monotonic() - started_monotonic) * 1000)

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    event = make_terminal_event(
        command=command,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        exit_code=completed.returncode,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        cwd=cwd,
        config=config,
        source=source,
    )
    created, event_path = store_event(home, event)
    state = "stored" if created else "deduped"
    print(f"ai-memory: {state} terminal hash {event['event_hash'][:12]} at {event_path}")

    try:
        sync_events(home, config, quiet=True)
    except Exception as exc:
        print(f"ai-memory: sync queued until network/API is available ({exc})", file=sys.stderr)

    return completed.returncode


def detect_history_file() -> Path | None:
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.extend(
            [
                Path(appdata) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt",
                Path(appdata) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "Visual Studio Code Host_history.txt",
            ]
        )
    candidates.extend([Path.home() / ".bash_history", Path.home() / ".zsh_history"])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def command_auth(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    if args.api_url:
        config["api_url"] = args.api_url.rstrip("/")
    config["token"] = args.token.strip()
    config["token_hash"] = sha256_text(args.token.strip())
    config["authed_at"] = utc_now()
    save_config(home, config)
    print(f"Saved CLI auth in {config_path(home)}")

    try:
        health = http_json("GET", f"{api_url(config)}/health", None, None)
        print(f"Connected to API: {health.get('service', api_url(config))}")
    except Exception as exc:
        print(f"Auth saved. API check failed, sync will retry later: {exc}", file=sys.stderr)
    return 0


def command_init(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    if args.api_url:
        config["api_url"] = args.api_url.rstrip("/")
    if args.project:
        config["project"] = args.project
    if args.repo:
        config["repository"] = args.repo
    if args.workspace:
        config["workspace_path"] = args.workspace
    save_config(home, config)

    token = require_token(config)
    payload = {
        "project": config.get("project") or "memory-project",
        "repository": config.get("repository") or "",
        "workspace_path": config.get("workspace_path") or ".",
        "integrations": ["github", "cli", "editor", "chat", "mcp"],
    }
    try:
        response = http_json("POST", f"{api_url(config)}/projects/init", payload, token)
        project = response.get("project", {})
        print(f"Initialized project: {project.get('id', payload['project'])}")
    except Exception as exc:
        print(f"Project config saved locally. Server init will need retry: {exc}", file=sys.stderr)
    print("Start terminal capture with: python -m ai_memory_cli watch")
    return 0


def command_workspace_connect(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    if args.path:
        config["workspace_path"] = args.path
    if args.repo:
        config["repository"] = args.repo
    save_config(home, config)

    token = require_token(config)
    payload = {
        "payload": {
            "source": "ai-memory-cli",
            "workspace_path": args.path,
            "repository": args.repo or config.get("repository") or "",
            "branch": args.branch,
            "editor": args.editor,
            "package_manager": args.package_manager,
            "cli_storage_home_hash": sha256_text(str(home)),
        }
    }
    response = http_json("POST", f"{api_url(config)}/workspace/connect", payload, token)
    event = response.get("event", {})
    print(f"Workspace connected: {event.get('id', 'saved')}")
    return 0


def command_mcp_connect(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    token = require_token(config)
    config["mcp_server"] = args.server
    save_config(home, config)
    payload = {
        "payload": {
            "source": "ai-memory-cli",
            "server": args.server,
            "project": config.get("project") or "",
            "repository": config.get("repository") or "",
            "cli_storage_home_hash": sha256_text(str(home)),
        }
    }
    response = http_json("POST", f"{api_url(config)}/mcp/connect", payload, token)
    event = response.get("event", {})
    print(f"MCP connected: {event.get('id', 'saved')}")
    return 0


def command_chat_connect(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    token = require_token(config)
    config["chat_provider"] = args.provider
    save_config(home, config)
    payload = {
        "payload": {
            "source": "ai-memory-cli",
            "provider": args.provider,
            "project": config.get("project") or "",
            "repository": config.get("repository") or "",
            "cli_storage_home_hash": sha256_text(str(home)),
        }
    }
    response = http_json("POST", f"{api_url(config)}/chat/connect", payload, token)
    event = response.get("event", {})
    print(f"Chat connected: {event.get('id', 'saved')}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    command = command_line(args.command)
    if not command:
        raise SystemExit("Pass a command after --, for example: python -m ai_memory_cli run -- python --version")
    home = cli_home()
    config = load_config(home)
    return capture_command(home, config, command, args.include_excluded, "run")


def command_watch(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    print("AI Memory watch mode. Type commands to run and capture. Type exit to stop.")
    while True:
        try:
            command = input("ai-memory> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not command:
            continue
        if command.lower() in {"exit", "quit"}:
            break
        capture_command(home, config, command, args.include_excluded, "watch")
    return 0


def command_history_import(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    history_path = Path(args.path).expanduser() if args.path else detect_history_file()
    if not history_path or not history_path.exists():
        raise SystemExit("No shell history file found. Pass --path <history-file>.")

    lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
    commands = [line.strip() for line in lines if line.strip()]
    commands = commands[-args.limit :]
    imported = 0
    now = utc_now()
    for command in commands:
        if is_excluded(command, config) and not args.include_excluded:
            continue
        event = make_terminal_event(
            command=command,
            stdout="",
            stderr="",
            exit_code=None,
            started_at=now,
            ended_at=now,
            duration_ms=0,
            cwd=Path.cwd(),
            config=config,
            source="history-import",
        )
        created, _ = store_event(home, event)
        if created:
            imported += 1
    print(f"Imported {imported} hashed history event(s) from {history_path}")
    try:
        sync_events(home, config, quiet=False)
    except Exception as exc:
        print(f"History hashes queued until network/API is available: {exc}", file=sys.stderr)
    return 0


def command_sync(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    try:
        sync_events(home, config, limit=args.limit, quiet=False)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Sync failed. Events remain queued: {exc}", file=sys.stderr)
        return 1
    return 0


def command_status(_: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    ensure_dirs(home)
    outbox_count = len(list((home / "outbox").glob("*.json")))
    event_count = len(list((home / "events").glob("*.json")))
    sent_count = len(list((home / "sent").glob("*.json")))
    print(f"AI Memory CLI {__version__}")
    print(f"Storage: {home}")
    print(f"API: {api_url(config)}")
    print(f"Project: {config.get('project') or '-'}")
    print(f"Repository: {config.get('repository') or '-'}")
    print(f"Workspace: {config.get('workspace_path') or '.'}")
    print(f"Token: {'saved' if config.get('token') else 'missing'}")
    print(f"Events: {event_count} total, {outbox_count} queued, {sent_count} synced receipts")
    return 0


def command_doctor(_: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    print(f"Python: {platform.python_version()}")
    print(f"Executable: {sys.executable}")
    print(f"Storage writable: {os.access(home, os.W_OK)} ({home})")
    print(f"API: {api_url(config)}")
    try:
        health = http_json("GET", f"{api_url(config)}/health", None, None)
        print(f"API health: ok ({health.get('service')})")
    except Exception as exc:
        print(f"API health: failed ({exc})")
    print(f"Shell: {selected_shell()}")
    print(f"PowerShell: {shutil.which('powershell') or shutil.which('pwsh') or '-'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-memory", description="AI Memory Python CLI")
    parser.add_argument("--version", action="version", version=f"ai-memory {__version__}")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    auth = subparsers.add_parser("auth", help="Save the app-issued CLI token.")
    auth.add_argument("--token", required=True, help="Token generated by the website.")
    auth.add_argument("--api-url", default=DEFAULT_API_URL, help="FastAPI base URL.")
    auth.set_defaults(func=command_auth)

    init = subparsers.add_parser("init", help="Save project config and call /projects/init.")
    init.add_argument("--project", required=True)
    init.add_argument("--repo", default="")
    init.add_argument("--workspace", default=".")
    init.add_argument("--api-url", default="")
    init.set_defaults(func=command_init)

    workspace = subparsers.add_parser("workspace", help="Workspace commands.")
    workspace_subparsers = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_connect = workspace_subparsers.add_parser("connect", help="Connect local workspace metadata.")
    workspace_connect.add_argument("--path", default=".")
    workspace_connect.add_argument("--repo", default="")
    workspace_connect.add_argument("--branch", default="main")
    workspace_connect.add_argument("--editor", default="vscode")
    workspace_connect.add_argument("--package-manager", default="pip")
    workspace_connect.set_defaults(func=command_workspace_connect)

    mcp = subparsers.add_parser("mcp", help="MCP integration commands.")
    mcp_subparsers = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_connect = mcp_subparsers.add_parser("connect", help="Connect MCP server metadata.")
    mcp_connect.add_argument("--server", required=True)
    mcp_connect.set_defaults(func=command_mcp_connect)

    chat = subparsers.add_parser("chat", help="Chat integration commands.")
    chat_subparsers = chat.add_subparsers(dest="chat_command", required=True)
    chat_connect = chat_subparsers.add_parser("connect", help="Connect chat app metadata.")
    chat_connect.add_argument("--provider", required=True)
    chat_connect.set_defaults(func=command_chat_connect)

    run = subparsers.add_parser("run", help="Run one command and store a hashed terminal event.")
    run.add_argument("--include-excluded", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=command_run)

    watch = subparsers.add_parser("watch", help="Start a managed terminal that captures commands and output.")
    watch.add_argument("--include-excluded", action="store_true")
    watch.set_defaults(func=command_watch)

    history = subparsers.add_parser("history", help="History import commands.")
    history_subparsers = history.add_subparsers(dest="history_command", required=True)
    history_import = history_subparsers.add_parser("import", help="Hash commands from an existing shell history file.")
    history_import.add_argument("--path", default="")
    history_import.add_argument("--limit", type=int, default=500)
    history_import.add_argument("--include-excluded", action="store_true")
    history_import.set_defaults(func=command_history_import)

    sync = subparsers.add_parser("sync", help="Sync queued terminal hashes to FastAPI.")
    sync.add_argument("--limit", type=int, default=50)
    sync.set_defaults(func=command_sync)

    status = subparsers.add_parser("status", help="Show local CLI state.")
    status.set_defaults(func=command_status)

    doctor = subparsers.add_parser("doctor", help="Check CLI, storage, and API health.")
    doctor.set_defaults(func=command_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
