from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import secrets
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
WINDOWS_AGENT_TASK_NAME = "AI Memory CLI Agent"
DEFAULT_AGENT_INTERVAL_SECONDS = 60
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
SHELL_NOT_FOUND_PATTERNS = [
    "is not recognized as an internal or external command",
    "is not recognized as the name of a cmdlet",
    "the system cannot find the file specified",
    "the syntax of the command is incorrect",
    "no such file or directory",
    "command not found",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def sha512_text(value: str) -> str:
    import hashlib

    return hashlib.sha512(value.encode("utf-8", errors="replace")).hexdigest()


def cli_home() -> Path:
    configured = os.getenv("AI_MEMORY_CLI_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".ai-memory-cli").resolve()


def ensure_dirs(home: Path) -> None:
    for folder in ["events", "outbox", "sent", "logs", "history"]:
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


def append_log(home: Path, message: str) -> None:
    ensure_dirs(home)
    log_path = home / "logs" / "agent.log"
    with log_path.open("a", encoding="utf-8") as file:
        file.write(f"{utc_now()} {message}\n")


def append_history(home: Path, event: dict[str, Any], state: str) -> None:
    ensure_dirs(home)
    timestamp = str(event.get("observed_at") or event.get("ended_at") or utc_now())
    day = timestamp[:10] if len(timestamp) >= 10 else utc_now()[:10]
    history_path = home / "history" / f"{day}.log"
    fields = [
        timestamp,
        f"state={state}",
        f"source={event.get('source', '-')}",
        f"exit={event.get('exit_code', '-')}",
        f"event={str(event.get('event_hash', ''))[:12]}",
        f"command_hash={event.get('command_hash', '-')}",
        f"output_hash={event.get('output_hash', '-')}",
        f"cwd={event.get('metadata', {}).get('cwd_tail', '-')}",
    ]
    with history_path.open("a", encoding="utf-8") as file:
        file.write(" ".join(fields) + "\n")


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


def ensure_local_identity(home: Path, config: dict[str, Any]) -> str:
    secret = str(config.get("local_identity_secret") or "").strip()
    if not secret:
        secret = secrets.token_urlsafe(96)
        config["local_identity_secret"] = secret
        config["local_identity_created_at"] = utc_now()

    local_user_hash = sha512_text(
        "\0".join(
            [
                secret,
                str(home),
                platform.node() or "unknown-host",
                getpass.getuser() or "unknown-user",
                platform.system(),
            ]
        )
    )
    config["local_user_hash"] = local_user_hash
    return local_user_hash


def client_identity(home: Path, config: dict[str, Any]) -> dict[str, Any]:
    local_user_hash = ensure_local_identity(home, config)
    return {
        "name": "ai-memory-cli",
        "version": __version__,
        "local_user_hash": local_user_hash,
        "user_hash": config.get("user_hash") or "",
        "github_user": config.get("github_user") or "",
        "session_id": config.get("session_id") or "",
        "storage_home_hash": sha256_text(str(home)),
        "hostname_hash": sha256_text(platform.node() or "unknown"),
        "username_hash": sha256_text(getpass.getuser() or "unknown"),
        "platform": platform.system(),
        "python": platform.python_version(),
        "auth_verified_at": config.get("auth_verified_at") or "",
    }


def agent_state_path(home: Path) -> Path:
    return home / "agent.json"


def scheduler_python_executable(background: bool = True) -> str:
    executable = Path(sys.executable)
    if os.name == "nt" and background:
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(executable)


def windows_startup_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA is not set; cannot locate the Windows Startup folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def windows_startup_script_path() -> Path:
    return windows_startup_dir() / "AI Memory CLI Agent.vbs"


def write_windows_startup_script(interval: int, limit: int) -> Path:
    startup_dir = windows_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)
    script_path = windows_startup_script_path()
    python_executable = scheduler_python_executable(background=False)
    command = f'"{python_executable}" -m ai_memory_cli agent run --interval {interval} --limit {limit}'
    escaped_command = command.replace('"', '""')
    script_path.write_text(
        "\n".join(
            [
                "Set shell = CreateObject(\"WScript.Shell\")",
                f"shell.Run \"{escaped_command}\", 0, False",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script_path


def start_detached_agent(interval: int, limit: int) -> int:
    python_executable = scheduler_python_executable(background=True)
    command = [
        python_executable,
        "-m",
        "ai_memory_cli",
        "agent",
        "run",
        "--interval",
        str(interval),
        "--limit",
        str(limit),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return int(process.pid)


def is_process_running(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False

    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid_int}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0 and str(pid_int) in result.stdout

    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def ensure_agent_started_once(home: Path, config: dict[str, Any]) -> None:
    agent_config = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    interval = int(agent_config.get("interval_seconds") or DEFAULT_AGENT_INTERVAL_SECONDS)
    limit = int(agent_config.get("limit") or 50)
    state = read_json(agent_state_path(home), {})

    if is_process_running(state.get("pid")):
        print(f"AI Memory sync agent already running: pid={state.get('pid')}")
        return

    if os.name == "nt":
        script_path = write_windows_startup_script(interval, limit)
        pid = start_detached_agent(interval, limit)
        config["agent"] = {
            **agent_config,
            "method": "startup",
            "interval_seconds": interval,
            "limit": limit,
            "startup_script": str(script_path),
            "last_started_pid": pid,
            "last_started_at": utc_now(),
        }
        save_config(home, config)
        append_log(home, f"auth started detached agent pid={pid}")
        print(f"AI Memory sync agent started once: pid={pid}")
        print(f"Startup sync installed at: {script_path}")
        return

    print("Automatic startup agent install is only implemented for Windows.")
    print("Start sync manually with: python -m ai_memory_cli agent run")


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def clean_watch_command(command: str) -> str:
    cleaned = command.strip()
    while cleaned.lower().startswith("ai-memory>"):
        cleaned = cleaned[len("ai-memory>") :].strip()
    cleaned = re.sub(r"^[A-Za-z]:\\[^>]*>\s*", "", cleaned).strip()
    return cleaned


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


def is_shell_not_found(stdout: str, stderr: str, exit_code: int | None) -> bool:
    if exit_code in (None, 0):
        return False
    combined = f"{stdout}\n{stderr}".lower()
    return any(pattern in combined for pattern in SHELL_NOT_FOUND_PATTERNS)


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


def describe_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(body) if body else {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if detail:
            return str(detail)
        if body:
            return body[:500]
    except Exception:
        pass
    return f"HTTP {exc.code} {exc.reason}"


def verify_cli_auth(home: Path, config: dict[str, Any], token: str) -> dict[str, Any]:
    identity = client_identity(home, config)
    response = http_json(
        "POST",
        f"{api_url(config)}/cli/auth/verify",
        {"client": identity},
        token,
    )
    if not response.get("verified"):
        raise RuntimeError("CLI token was not verified by the backend.")

    config["token"] = token
    config["token_hash"] = sha256_text(token)
    config["token_tail"] = response.get("token_tail") or ""
    config["session_id"] = response.get("session_id") or ""
    config["github_user"] = response.get("github_user") or response.get("github_account_name") or ""
    config["user_hash"] = response.get("user_hash") or ""
    config["bound_local_user_hash"] = response.get("bound_local_user_hash") or identity["local_user_hash"]
    config["auth_verified_at"] = response.get("verified_at") or utc_now()
    config["server_account_storage_dir"] = response.get("account_storage_dir") or ""
    return response


def require_verified_auth(home: Path, config: dict[str, Any]) -> str:
    token = require_token(config)
    if config.get("auth_verified_at") and config.get("user_hash") and config.get("local_user_hash"):
        return token

    try:
        verify_cli_auth(home, config, token)
        save_config(home, config)
        return token
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"CLI auth is not verified: {describe_http_error(exc)}") from exc
    except Exception as exc:
        raise SystemExit(f"CLI auth is not verified. Run website auth again when the local server is available: {exc}") from exc


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
    if not (config.get("auth_verified_at") and config.get("user_hash")):
        try:
            verify_cli_auth(home, config, token)
            save_config(home, config)
        except Exception as exc:
            if not quiet:
                print(f"CLI auth is not verified yet. Events remain queued: {exc}")
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
        "client": client_identity(home, config),
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
    require_verified_auth(home, config)
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

    if is_shell_not_found(completed.stdout or "", completed.stderr or "", completed.returncode):
        print("ai-memory: skipped invalid command; nothing was stored.")
        return completed.returncode

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
    append_history(home, event, state)
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

    token = args.token.strip()
    config["pending_token_hash"] = sha256_text(token)
    try:
        response = verify_cli_auth(home, config, token)
    except urllib.error.HTTPError as exc:
        save_config(home, config)
        raise SystemExit(f"CLI auth failed: {describe_http_error(exc)}") from exc
    except Exception as exc:
        save_config(home, config)
        raise SystemExit(f"CLI auth failed. Keep the local FastAPI server running and generate a fresh website token: {exc}") from exc

    config["authed_at"] = utc_now()
    save_config(home, config)
    print(f"Saved CLI auth in {config_path(home)}")
    print(f"GitHub account: {response.get('github_user') or config.get('github_user') or '-'}")
    print(f"Local user hash: {str(config.get('user_hash') or '')[:24]}...")
    if response.get("account_storage_dir"):
        print(f"Server account storage: {response['account_storage_dir']}")

    if not args.no_agent:
        ensure_agent_started_once(home, config)

    try:
        sync_events(home, config, quiet=True)
    except Exception as exc:
        print(f"Auth saved. Sync will retry later: {exc}", file=sys.stderr)
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

    token = require_verified_auth(home, config)
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
    print("Start terminal capture with: watch")
    return 0


def command_workspace_connect(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    if args.path:
        config["workspace_path"] = args.path
    if args.repo:
        config["repository"] = args.repo
    save_config(home, config)

    token = require_verified_auth(home, config)
    identity = client_identity(home, config)
    payload = {
        "payload": {
            "source": "ai-memory-cli",
            "workspace_path": args.path,
            "repository": args.repo or config.get("repository") or "",
            "branch": args.branch,
            "editor": args.editor,
            "package_manager": args.package_manager,
            "cli_storage_home_hash": sha256_text(str(home)),
            "local_user_hash": identity["local_user_hash"],
            "user_hash": identity["user_hash"],
            "github_user": identity["github_user"],
        }
    }
    response = http_json("POST", f"{api_url(config)}/workspace/connect", payload, token)
    event = response.get("event", {})
    print(f"Workspace connected: {event.get('id', 'saved')}")
    return 0


def command_mcp_connect(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    token = require_verified_auth(home, config)
    identity = client_identity(home, config)
    config["mcp_server"] = args.server
    save_config(home, config)
    payload = {
        "payload": {
            "source": "ai-memory-cli",
            "server": args.server,
            "project": config.get("project") or "",
            "repository": config.get("repository") or "",
            "cli_storage_home_hash": sha256_text(str(home)),
            "local_user_hash": identity["local_user_hash"],
            "user_hash": identity["user_hash"],
            "github_user": identity["github_user"],
        }
    }
    response = http_json("POST", f"{api_url(config)}/mcp/connect", payload, token)
    event = response.get("event", {})
    print(f"MCP connected: {event.get('id', 'saved')}")
    return 0


def command_chat_connect(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    token = require_verified_auth(home, config)
    identity = client_identity(home, config)
    config["chat_provider"] = args.provider
    save_config(home, config)
    payload = {
        "payload": {
            "source": "ai-memory-cli",
            "provider": args.provider,
            "project": config.get("project") or "",
            "repository": config.get("repository") or "",
            "cli_storage_home_hash": sha256_text(str(home)),
            "local_user_hash": identity["local_user_hash"],
            "user_hash": identity["user_hash"],
            "github_user": identity["github_user"],
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
        cleaned_command = clean_watch_command(command)
        if cleaned_command != command:
            if not cleaned_command:
                print("ai-memory: skipped pasted prompt without a command.")
                continue
            print(f"ai-memory: using command without pasted prompt: {cleaned_command}")
            command = cleaned_command
        if command.lower() in {"exit", "quit"}:
            break
        capture_command(home, config, command, args.include_excluded, "watch")
    return 0


def command_history_import(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    require_verified_auth(home, config)
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


def command_agent_run(args: argparse.Namespace) -> int:
    home = cli_home()
    ensure_dirs(home)
    interval = max(10, int(args.interval))
    limit = max(1, int(args.limit))
    state = {
        "pid": os.getpid(),
        "version": __version__,
        "started_at": utc_now(),
        "interval_seconds": interval,
        "limit": limit,
        "mode": "once" if args.once else "loop",
    }
    write_json(agent_state_path(home), state)
    append_log(home, f"agent started pid={os.getpid()} interval={interval}s limit={limit}")

    try:
        while True:
            config = load_config(home)
            try:
                synced = sync_events(home, config, limit=limit, quiet=True)
                if synced:
                    append_log(home, f"synced {synced} terminal event(s)")
            except Exception as exc:
                append_log(home, f"sync failed: {exc}")

            if args.once:
                break
            time.sleep(interval)
    finally:
        state["stopped_at"] = utc_now()
        write_json(agent_state_path(home), state)
        append_log(home, "agent stopped")

    return 0


def command_agent_install(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("agent install currently supports Windows Task Scheduler only.")

    home = cli_home()
    config = load_config(home)
    config["agent"] = {
        "task_name": args.task_name,
        "interval_seconds": args.interval,
        "limit": args.limit,
        "installed_at": utc_now(),
    }
    save_config(home, config)

    if args.method in {"auto", "task"}:
        python_executable = scheduler_python_executable(background=not args.console)
        task_command = (
            f'"{python_executable}" -m ai_memory_cli agent run '
            f"--interval {int(args.interval)} --limit {int(args.limit)}"
        )
        result = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                args.task_name,
                "/SC",
                "ONLOGON",
                "/TR",
                task_command,
                "/F",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            append_log(home, f"installed Windows scheduled task: {args.task_name}")
            print(f"Installed startup agent task: {args.task_name}")
            print("It starts when you log in. Start it now with:")
            print("python -m ai_memory_cli agent start")
            return 0

        if args.method == "task":
            raise SystemExit((result.stderr or result.stdout).strip())

        print("Task Scheduler install failed; falling back to user Startup folder.")
        print((result.stderr or result.stdout).strip())

    script_path = write_windows_startup_script(int(args.interval), int(args.limit))
    append_log(home, f"installed Windows startup script: {script_path}")
    print(f"Installed startup agent script: {script_path}")
    print("It starts when you log in. Start it now with:")
    print("python -m ai_memory_cli agent run")
    return 0


def command_agent_uninstall(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("agent uninstall currently supports Windows Task Scheduler only.")

    removed = False
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", args.task_name, "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        removed = True

    script_path = windows_startup_script_path()
    if script_path.exists():
        script_path.unlink()
        removed = True

    append_log(cli_home(), f"uninstalled Windows scheduled task: {args.task_name}")
    if removed:
        print("Removed startup agent registration.")
    else:
        print("No startup agent registration was found.")
    return 0


def command_agent_start(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("agent start currently supports Windows Task Scheduler only.")

    home = cli_home()
    config = load_config(home)
    state = read_json(agent_state_path(home), {})
    if is_process_running(state.get("pid")):
        print(f"Agent already running: pid={state.get('pid')}")
        return 0

    result = subprocess.run(
        ["schtasks", "/Run", "/TN", args.task_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        print(f"Started agent task: {args.task_name}")
        return 0

    agent_config = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    interval = int(agent_config.get("interval_seconds") or DEFAULT_AGENT_INTERVAL_SECONDS)
    limit = int(agent_config.get("limit") or 50)
    pid = start_detached_agent(interval, limit)
    append_log(home, f"started detached agent pid={pid}")
    print(f"Started detached agent process: pid={pid}")
    return 0


def command_agent_stop(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("agent stop currently supports Windows Task Scheduler only.")

    home = cli_home()
    result = subprocess.run(
        ["schtasks", "/End", "/TN", args.task_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        append_log(home, f"stopped Windows scheduled task: {args.task_name}")
        print(f"Stopped agent task: {args.task_name}")
        return 0

    state = read_json(agent_state_path(home), {})
    pid = state.get("pid")
    if not pid:
        print("No running detached agent pid was found.")
        return 0

    kill = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if kill.returncode != 0:
        raise SystemExit((kill.stderr or kill.stdout).strip())
    append_log(home, f"stopped detached agent pid={pid}")
    print(f"Stopped detached agent process: pid={pid}")
    return 0


def command_agent_status(args: argparse.Namespace) -> int:
    home = cli_home()
    config = load_config(home)
    state = read_json(agent_state_path(home), {})
    print(f"Storage: {home}")
    print(f"API: {api_url(config)}")
    print(f"Token: {'saved' if config.get('token') else 'missing'}")
    if state:
        print(f"Agent state: pid={state.get('pid', '-')} started={state.get('started_at', '-')}")
    else:
        print("Agent state: no local agent state file yet")

    if os.name != "nt":
        return 0

    result = subprocess.run(
        ["schtasks", "/Query", "/TN", args.task_name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"Windows task: not installed ({args.task_name})")
    else:
        print(result.stdout.strip())

    script_path = windows_startup_script_path()
    print(f"Startup script: {'installed' if script_path.exists() else 'not installed'} ({script_path})")
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
    print(f"Token: {'verified' if config.get('auth_verified_at') and config.get('user_hash') else 'saved' if config.get('token') else 'missing'}")
    print(f"GitHub account: {config.get('github_user') or '-'}")
    print(f"User hash: {str(config.get('user_hash') or '-')[:24]}{'...' if config.get('user_hash') else ''}")
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
    auth.add_argument("--no-agent", action="store_true", help="Do not auto-start the background sync agent after auth.")
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
    watch.add_argument("--version", action="version", version=f"ai-memory {__version__}")
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

    agent = subparsers.add_parser("agent", help="Background sync agent commands.")
    agent_subparsers = agent.add_subparsers(dest="agent_command", required=True)

    agent_run = agent_subparsers.add_parser("run", help="Run the background sync loop.")
    agent_run.add_argument("--interval", type=int, default=DEFAULT_AGENT_INTERVAL_SECONDS)
    agent_run.add_argument("--limit", type=int, default=50)
    agent_run.add_argument("--once", action="store_true")
    agent_run.set_defaults(func=command_agent_run)

    agent_install = agent_subparsers.add_parser("install", help="Install Windows startup task for the sync agent.")
    agent_install.add_argument("--interval", type=int, default=DEFAULT_AGENT_INTERVAL_SECONDS)
    agent_install.add_argument("--limit", type=int, default=50)
    agent_install.add_argument("--task-name", default=WINDOWS_AGENT_TASK_NAME)
    agent_install.add_argument("--method", choices=["auto", "task", "startup"], default="auto")
    agent_install.add_argument("--console", action="store_true", help="Use python.exe instead of pythonw.exe for the scheduled task.")
    agent_install.set_defaults(func=command_agent_install)

    agent_uninstall = agent_subparsers.add_parser("uninstall", help="Remove Windows startup task for the sync agent.")
    agent_uninstall.add_argument("--task-name", default=WINDOWS_AGENT_TASK_NAME)
    agent_uninstall.set_defaults(func=command_agent_uninstall)

    agent_start = agent_subparsers.add_parser("start", help="Start the installed Windows agent task now.")
    agent_start.add_argument("--task-name", default=WINDOWS_AGENT_TASK_NAME)
    agent_start.set_defaults(func=command_agent_start)

    agent_stop = agent_subparsers.add_parser("stop", help="Stop the installed Windows agent task.")
    agent_stop.add_argument("--task-name", default=WINDOWS_AGENT_TASK_NAME)
    agent_stop.set_defaults(func=command_agent_stop)

    agent_status = agent_subparsers.add_parser("status", help="Show background agent and Windows task state.")
    agent_status.add_argument("--task-name", default=WINDOWS_AGENT_TASK_NAME)
    agent_status.set_defaults(func=command_agent_status)

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


def watch_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watch", description="Start AI Memory terminal capture.")
    parser.add_argument("--include-excluded", action="store_true")
    parser.add_argument("--version", action="version", version=f"ai-memory {__version__}")
    args = parser.parse_args(argv)
    try:
        return command_watch(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
