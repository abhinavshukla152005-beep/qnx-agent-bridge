#!/usr/bin/env python3
"""A minimal QNX 8.0 serial console, used to test SerialTarget offline.

It mimics the parts of a QSTI guest the bridge touches: login, a root shell
prompt, command echo, and the four utilities behind the MCP tools.
"""

from __future__ import annotations

import sys

USER = "root"
PASSWORD = "root"

PROCESSES = [
    (1, "procnto-smp-instr", "READY", 1, "0f"),
    (8194, "slogger2", "RECEIVE", 3, "10f"),
    (12290, "devc-pty", "RECEIVE", 1, "10f"),
    (16387, "io-pkt-v6-hc", "RECEIVE", 2, "21f"),
    (24580, "qnx-agent-demo", "RUNNING", 4, "16r"),
]

THREADS = [
    (24580, 1, "qnx-agent-demo", "RECEIVE", "MsgReceive", "16r"),
    (24580, 2, "qnx-agent-demo", "READY", "", "16r"),
    (24580, 3, "qnx-agent-demo", "NANOSLEEP", "nanosleep", "10f"),
    (24580, 4, "qnx-agent-demo", "RUNNING", "", "16r"),
]

CMDLINE = "qnx-agent-demo\x00--watch\x00/sensors\x00"

LOG = """Jan 12 10:41:07  24580  INFO   agent-demo: target link established
Jan 12 10:41:09  24580  INFO   agent-demo: sampling process table
Jan 12 10:41:12  24580  WARNING agent-demo: thread 3 blocked on MsgReceive
Jan 12 10:41:15  24580  ERROR  agent-demo: watchdog deadline missed
"""


def pidin(args: list[str]) -> str:
    if "-F" in args and "%b" in args[args.index("-F") + 1]:
        return "".join(
            f"{pid}|{tid}|{name}|{state}|{blocked}|{prio}\n"
            for pid, tid, name, state, blocked, prio in THREADS
        )
    return "".join(
        f"{pid}|{name}|{state}|{threads}|{prio}\n"
        for pid, name, state, threads, prio in PROCESSES
    )


def slog2info(args: list[str]) -> str:
    if args[:1] == ["-s"] and len(args) > 1:
        wanted = args[1].upper()
        rows = [row for row in LOG.splitlines() if wanted[:4] in row.upper()]
        return "\n".join(rows) + "\n"
    return LOG


def run(command: str) -> str:
    parts = command.split()
    if not parts:
        return ""
    utility, args = parts[0], parts[1:]
    if utility == "pidin":
        return pidin(args)
    if utility == "slog2info":
        return slog2info(args)
    if utility == "uname":
        return "QNX qemu-target 8.0.0 2026/04/11-12:00:00EDT x86pc x86_64\n"
    if utility == "cat" and args:
        if args[0].startswith("/proc/") and args[0].endswith("/cmdline"):
            return CMDLINE
        return f"cat: {args[0]}: No such file or directory\n"
    return f"{utility}: command not found\n"


def main() -> None:
    out = sys.stdout
    out.write("QNX Neutrino 8.0.0 2026/04/11-12:00:00EDT\r\n")
    out.write("login: ")
    out.flush()

    user = sys.stdin.readline().rstrip("\r\n")
    out.write(f"{user}\r\nPassword: ")
    out.flush()
    password = sys.stdin.readline().rstrip("\r\n")
    if user != USER or password != PASSWORD:
        out.write("\r\nLogin incorrect\r\nlogin: ")
        out.flush()
        return
    out.write("\r\nWelcome to QNX Neutrino 8.0.0\r\n# ")
    out.flush()

    while True:
        line = sys.stdin.readline()
        if not line:
            return
        command = line.rstrip("\r\n")
        out.write(f"{command}\r\n")
        for part in command.split("; "):
            if part == "echo":
                out.write("\r\n")
            elif part.startswith("echo "):
                out.write(part[len("echo ") :] + "\r\n")
            else:
                out.write(run(part))
        out.write("# ")
        out.flush()


if __name__ == "__main__":
    main()
