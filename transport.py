"""Execution backends for a QNX 8.0 target."""

from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field


class TargetError(RuntimeError):
    """Raised when the target cannot execute a command."""


class Target:
    """Runs one read-only command on the target and returns stdout."""

    def run(self, argv: list[str], timeout: int = 20) -> str:
        raise NotImplementedError


@dataclass
class SSHTarget(Target):
    """A QNX guest reachable over SSH, e.g. QEMU with a forwarded port."""

    host: str
    user: str | None = None
    port: int = 22
    timeout: int = 20

    def run(self, argv: list[str], timeout: int | None = None) -> str:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.port),
        ]
        if self.user:
            command += ["-l", self.user]
        command += [self.host, " ".join(shlex.quote(part) for part in argv)]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            msg = f"ssh failed: {error}"
            raise TargetError(msg) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise TargetError(detail or f"exit status {result.returncode}")
        return result.stdout


class _Console:
    """Line-less reader over a child process's stdio.

    The QNX console prompts (`login:`, `# `) carry no trailing newline, so
    line-oriented reading would block before the prompt is ever returned.
    """

    def __init__(self, command: list[str]) -> None:
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=0,
            )
        except OSError as error:
            msg = f"cannot start console command: {error}"
            raise TargetError(msg) from error
        self._buffer = ""
        self._condition = threading.Condition()
        self._closed = False
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        stream = self._proc.stdout
        while True:
            char = stream.read(1)
            if not char:
                break
            with self._condition:
                self._buffer += char
                self._condition.notify_all()
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def send(self, line: str) -> None:
        if self._proc.stdin is None:
            msg = "console stdin is closed"
            raise TargetError(msg)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def read_until(self, pattern: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                match = re.search(pattern, self._buffer)
                if match:
                    chunk = self._buffer[: match.end()]
                    self._buffer = self._buffer[match.end() :]
                    return chunk
                if self._closed:
                    chunk = self._buffer
                    self._buffer = ""
                    msg = f"console closed while waiting for {pattern!r}: {chunk.strip()!r}"
                    raise TargetError(msg)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    msg = f"timed out waiting for {pattern!r}"
                    raise TargetError(msg)
                self._condition.wait(remaining)

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()


@dataclass
class SerialTarget(Target):
    """Drives the QNX console on a QEMU serial line.

    The Quick Start Target Image documents the serial console as its
    interface; this transport needs no sshd on the guest.
    """

    command: list[str]
    user: str = "root"
    password: str = "root"
    login: bool = True
    timeout: int = 30

    def __post_init__(self) -> None:
        self._console = _Console(self.command)
        self._sequence = 0
        self._logged_in = False

    def _wait_for_prompt(self) -> None:
        self._console.read_until(r"[#$] ", self.timeout)

    def _ensure_login(self) -> None:
        """Log in if the console asks; tolerate auto-login and a blank password."""
        if self._logged_in:
            return
        chunk = self._console.read_until(r"(?i)login:\s*|[#$] ", self.timeout)
        if re.search(r"[#$] $", chunk):
            self._logged_in = True
            return
        if not self.login:
            self._logged_in = True
            return
        for password in (self.password, ""):
            self._console.send(self.user)
            reply = self._console.read_until(
                r"(?i)password:\s*|login:\s*|[#$] ", self.timeout
            )
            if "password:" in reply.lower():
                self._console.send(password)
                reply = self._console.read_until(r"(?i)login:\s*|[#$] ", self.timeout)
            if re.search(r"[#$] $", reply):
                self._logged_in = True
                return
        msg = "console login rejected"
        raise TargetError(msg)

    def run(self, argv: list[str], timeout: int | None = None) -> str:
        self._ensure_login()
        self._sequence += 1
        token = f"__QNXBRIDGE_{self._sequence:04d}__"
        command = " ".join(shlex.quote(part) for part in argv)
        # The bare echo terminates an unterminated line (e.g. /proc/<pid>/cmdline
        # is NUL-separated with no trailing newline), so the sentinel always
        # starts a line of its own and cannot be confused with the echo of the
        # command we sent.
        sent = f"{command}; echo; echo {token}"
        self._console.send(sent)
        chunk = self._console.read_until(
            rf"(?m)^{token}\r?$", timeout or self.timeout
        )
        return self._clean(chunk, sent, token)

    @staticmethod
    def _clean(chunk: str, sent_command: str, token: str) -> str:
        """Drop the echoed command line, the sentinel and shell prompts."""
        chunk = chunk.lstrip("\r\n")
        chunk = re.sub(r"^[#$] ", "", chunk, count=1)
        kept = []
        for raw in chunk.splitlines():
            text = raw.rstrip("\r")
            stripped = text.strip()
            if not stripped or stripped == token or stripped == sent_command:
                continue
            if re.fullmatch(r"[#$]", stripped):
                continue
            kept.append(text)
        return "\n".join(kept) + ("\n" if kept else "")

    def __del__(self) -> None:
        console = getattr(self, "_console", None)
        if console is not None:
            console.close()


@dataclass
class MockTarget(Target):
    """Canned QNX output so the bridge can be exercised without an SDP licence."""

    responses: dict[str, str] = field(default_factory=dict)

    def run(self, argv: list[str], timeout: int | None = None) -> str:
        key = argv[0]
        if key not in self.responses:
            msg = f"mock target has no response for {key!r}"
            raise TargetError(msg)
        if key == "pidin" and "-F" in argv:
            fmt = argv[argv.index("-F") + 1]
            if "%b" in fmt:
                return self.responses["pidin-threads"]
        return self.responses[key]


MOCK_RESPONSES: dict[str, str] = {
    "uname": "QNX qemu-target 8.0.0 2026/04/11-12:00:00EDT x86pc x86_64\n",
    "pidin": (
        "1|procnto-smp-instr|READY|1|0f\n"
        "8194|slogger2|RECEIVE|3|10f\n"
        "12290|devc-pty|RECEIVE|1|10f\n"
        "16387|io-pkt-v6-hc|RECEIVE|2|21f\n"
        "24580|qnx-agent-demo|RUNNING|4|16r\n"
    ),
    "pidin-threads": (
        "24580|1|qnx-agent-demo|RECEIVE|MsgReceive|16r\n"
        "24580|2|qnx-agent-demo|READY||16r\n"
        "24580|3|qnx-agent-demo|NANOSLEEP|nanosleep|10f\n"
        "24580|4|qnx-agent-demo|RUNNING||16r\n"
    ),
    "cat": "qnx-agent-demo\x00--watch\x00/sensors\x00",
    "slog2info": (
        "Jan 12 10:41:07  24580  INFO   agent-demo: target link established\n"
        "Jan 12 10:41:09  24580  INFO   agent-demo: sampling process table\n"
        "Jan 12 10:41:12  24580  WARNING agent-demo: thread 3 blocked on MsgReceive\n"
        "Jan 12 10:41:15  24580  ERROR  agent-demo: watchdog deadline missed\n"
    ),
}


def build_mock() -> MockTarget:
    return MockTarget(responses=dict(MOCK_RESPONSES))
