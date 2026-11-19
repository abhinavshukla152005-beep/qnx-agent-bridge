#!/usr/bin/env python3
"""MCP server that exposes a running QNX 8.0 target to an LLM agent.

The agent gets read-only visibility into process state, system log and
scheduling information. Every tool maps to a fixed QNX utility invocation;
there is no shell and no way to mutate the target.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Any

from transport import SSHTarget, SerialTarget, Target, TargetError, build_mock

PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "qnx-agent-bridge", "version": "0.1.0"}

# %a pid, %N process name, %J thread state, %T thread count,
# %p priority plus scheduling policy (rendered like "10f").
PIDIN_FORMAT = "%a|%N|%J|%T|%p"
# %b thread id, %B blocked-on, the rest as above.
THREAD_FORMAT = "%a|%b|%N|%J|%B|%p"
PRIORITY_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]*)\s*$")
POLICY_NAMES = {"f": "FIFO", "r": "RR", "s": "SPORADIC", "o": "OTHER"}
SEVERITY_ORDER = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug1", "debug2"]
SEVERITY_ALIASES = {
    "SHUTDOWN": "emerg",
    "EMERG": "emerg",
    "EMERGENCY": "emerg",
    "ALERT": "alert",
    "CRITICAL": "crit",
    "CRIT": "crit",
    "ERROR": "err",
    "ERR": "err",
    "WARNING": "warning",
    "WARN": "warning",
    "NOTICE": "notice",
    "INFO": "info",
    "DEBUG1": "debug1",
    "DEBUG2": "debug2",
}
SEVERITY_TOKEN_RE = re.compile(
    r"\b(" + "|".join(sorted(SEVERITY_ALIASES, key=len, reverse=True)) + r")\b"
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "qnx_list_processes",
        "description": (
            "List every process on the QNX target with its state, thread count, "
            "priority and scheduling policy."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "qnx_process_detail",
        "description": (
            "Return one process: its scheduling row, the argv from "
            "/proc/<pid>/cmdline, and every thread with its state and blocked-on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"pid": {"type": "integer", "minimum": 1}},
            "required": ["pid"],
            "additionalProperties": False,
        },
    },
    {
        "name": "qnx_system_log",
        "description": "Tail the slog2 system log, optionally filtered by minimum severity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 40,
                },
                "severity": {"type": "string", "enum": SEVERITY_ORDER},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "qnx_system_info",
        "description": "Return the kernel version and the current process count.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def split_priority(raw: str) -> tuple[int | None, str | None]:
    """Split pidin's ``%p`` field ("10f") into priority and policy."""
    match = PRIORITY_RE.match(raw)
    if not match:
        return None, raw.strip() or None
    priority, letters = match.groups()
    policy = POLICY_NAMES.get(letters.lower(), letters.upper() or None)
    return int(priority), policy


def parse_pidin(output: str) -> list[dict[str, Any]]:
    processes = []
    for line in output.splitlines():
        fields = line.strip().split("|")
        if len(fields) != 5 or not fields[0].isdigit():
            continue
        pid, name, state, threads, prio = fields
        priority, policy = split_priority(prio)
        processes.append(
            {
                "pid": int(pid),
                "name": name,
                "state": state,
                "threads": int(threads),
                "priority": priority,
                "policy": policy,
            }
        )
    return sorted(processes, key=lambda item: item["pid"])


def parse_threads(output: str) -> list[dict[str, Any]]:
    threads = []
    for line in output.splitlines():
        fields = line.strip().split("|")
        if len(fields) != 6 or not fields[0].isdigit():
            continue
        pid, tid, name, state, blocked, prio = fields
        if not tid.strip().isdigit():
            continue
        priority, policy = split_priority(prio)
        threads.append(
            {
                "pid": int(pid),
                "tid": int(tid),
                "name": name,
                "state": state,
                "blocked": blocked.strip() or None,
                "priority": priority,
                "policy": policy,
            }
        )
    return threads


def parse_cmdline(raw: str) -> list[str]:
    """Split /proc/<pid>/cmdline, which separates arguments with NUL bytes."""
    text = raw.replace("\r", "").replace("\n", "")
    parts = [part for part in text.split("\x00") if part.strip()]
    if len(parts) > 1:
        return parts
    return parts[0].split() if parts else []


def _severity_rank(line: str) -> int:
    """Rank a slog2info line by severity; unknown lines sort last."""
    match = SEVERITY_TOKEN_RE.search(line.upper())
    if not match:
        return len(SEVERITY_ORDER)
    return SEVERITY_ORDER.index(SEVERITY_ALIASES[match.group(1)])


def _text(payload: Any) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(payload, indent=2)}]


def call_tool(name: str, arguments: dict[str, Any], target: Target) -> list[dict[str, str]]:
    if name == "qnx_list_processes":
        return _text(parse_pidin(target.run(["pidin", "-F", PIDIN_FORMAT])))

    if name == "qnx_process_detail":
        pid = arguments.get("pid")
        if not isinstance(pid, int) or pid < 1:
            msg = "pid must be a positive integer"
            raise ValueError(msg)
        process = [
            item
            for item in parse_pidin(target.run(["pidin", "-p", str(pid), "-F", PIDIN_FORMAT]))
            if item["pid"] == pid
        ]
        if not process:
            msg = f"no process with pid {pid}"
            raise ValueError(msg)
        threads = parse_threads(target.run(["pidin", "-p", str(pid), "-F", THREAD_FORMAT]))
        cmdline = parse_cmdline(target.run(["cat", f"/proc/{pid}/cmdline"]))
        return _text(
            {"process": process[0], "cmdline": cmdline, "threads": threads}
        )

    if name == "qnx_system_log":
        lines = arguments.get("lines", 40)
        if not isinstance(lines, int) or not 1 <= lines <= 200:
            msg = "lines must be between 1 and 200"
            raise ValueError(msg)
        severity = arguments.get("severity")
        if severity is not None and severity not in SEVERITY_ORDER:
            msg = f"unknown severity {severity!r}"
            raise ValueError(msg)
        output = target.run(["slog2info"]).strip().splitlines()
        if severity is not None:
            cutoff = SEVERITY_ORDER.index(severity)
            output = [line for line in output if _severity_rank(line) <= cutoff]
        return _text({"entries": output[-lines:], "total": len(output)})

    if name == "qnx_system_info":
        kernel = target.run(["uname", "-a"]).strip()
        processes = parse_pidin(target.run(["pidin", "-F", PIDIN_FORMAT]))
        return _text({"kernel": kernel, "process_count": len(processes)})

    msg = f"unknown tool {name!r}"
    raise ValueError(msg)


def response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any], target: Target) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if request_id is None:
        return None

    if method == "initialize":
        return response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )

    if method == "ping":
        return response(request_id, {})

    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            content = call_tool(name, arguments, target)
        except (TargetError, ValueError) as exc:
            return response(request_id, {"content": _text({"error": str(exc)}), "isError": True})
        return response(request_id, {"content": content, "isError": False})

    return error(request_id, -32601, f"method not found: {method}")


def build_target(argv: list[str]) -> Target:
    if "--mock" in argv or not argv:
        return build_mock()
    serial = None
    host = None
    user = None
    port = 22
    for item in argv:
        if item.startswith("--serial="):
            serial = item.split("=", 1)[1]
        elif item.startswith("--host="):
            host = item.split("=", 1)[1]
        elif item.startswith("--user="):
            user = item.split("=", 1)[1]
        elif item.startswith("--port="):
            port = int(item.split("=", 1)[1])
    if serial is not None:
        return SerialTarget(command=shlex.split(serial))
    if host is None:
        msg = (
            "usage: server.py [--mock] | [--serial='<command>'] | "
            "[--host=<addr>] [--user=<login>] [--port=22]"
        )
        raise SystemExit(msg)
    return SSHTarget(host=host, user=user, port=port)


def main() -> None:
    try:
        target = build_target(sys.argv[1:])
    except TargetError as exc:
        raise SystemExit(f"qnx-agent-bridge: {exc}") from exc
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = handle(request, target)
        if result is not None:
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
