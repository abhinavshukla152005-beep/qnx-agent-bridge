# QNX Agent Bridge

An MCP server that gives an LLM agent read-only visibility into a running
QNX 8.0 target: process table, per-thread state, process argv and the `slog2`
system log.

Built for the **QNX Global Hackathon 2026**, category *Developer Tooling &
Host Tools*: a host-side tool that supports software targeting QNX 8.0.

## Why

Debugging an embedded target is still a human reading terminal output. The
bridge turns the same QNX utilities an engineer would run by hand (`pidin`,
`slog2info`, `/proc/<pid>/cmdline`) into structured MCP tools, so an agent can
answer "which thread missed its deadline" without a person copy-pasting
`pidin` output.

Everything the server can do is read-only. Each tool maps to one fixed utility
invocation; there is no shell, no user-supplied command, and no write path to
the target.

## Architecture

```
agent  --stdio JSON-RPC-->  server.py  --ssh-->  QNX 8.0 guest (QEMU or board)
                                                pidin / slog2info / procfs
```

## Tools

| Tool | Backing command |
| --- | --- |
| `qnx_list_processes` | `pidin -F "%a\|%N\|%J\|%T\|%p"` |
| `qnx_process_detail` | `pidin -p <pid> -F "%a\|%b\|%N\|%J\|%B\|%p"`, `/proc/<pid>/cmdline` |
| `qnx_system_log` | `slog2info` (buffered dump; severity filtered in the bridge) |
| `qnx_system_info` | `uname -a`, process count from `pidin` |

The `%p` field carries priority and scheduling policy together (`16r`), so the
parser splits it into `priority: 16`, `policy: "RR"`.

`slog2info` is run without `-w`: `-w` streams new messages and never exits, which
would hang the tool call. Severity filtering happens in the bridge, because
`slog2info -s` only prints the numeric level rather than filtering on it.

## Run

Mock target (no QNX licence needed):

```sh
python server.py --mock
```

Live target over SSH:

```sh
python server.py --host=127.0.0.1 --port=2222 --user=qnx
```

Live target over its serial console (no sshd required):

```sh
python server.py --serial="bash qemu/run-qnx.sh ~/qnx800/images/qemu/qemu"
```

Smoke test (covers both the mock and a scripted serial console):

```sh
python tests/smoke.py
```

Demo client — drives the server like an agent and prints a readable summary:

```sh
python tests/demo_client.py --mock
```

```
target      QNX qemu-target 8.0.0 2026/04/11-12:00:00EDT x86pc x86_64
processes   5

process table
      pid  threads  prio  policy  state      name
        1        1     0  FIFO    READY      procnto-smp-instr
     8194        3    10  FIFO    RECEIVE    slogger2
    12290        1    10  FIFO    RECEIVE    devc-pty
    16387        2    21  FIFO    RECEIVE    io-pkt-v6-hc
    24580        4    16  RR      RUNNING    qnx-agent-demo

qnx-agent-demo (pid 24580)
  argv      qnx-agent-demo --watch /sensors
  thread 1: RECEIVE blocked on MsgReceive prio 16 RR
  thread 2: READY prio 16 RR
  thread 3: NANOSLEEP blocked on nanosleep prio 10 FIFO
  thread 4: RUNNING prio 16 RR

system log (2 at warning or worse)
  Jan 12 10:41:12  24580  WARNING agent-demo: thread 3 blocked on MsgReceive
  Jan 12 10:41:15  24580  ERROR  agent-demo: watchdog deadline missed
```

## Booting the QNX target

`qemu/install-sdp.sh` installs QNX Software Center and the 8.0 baseline
headlessly on Windows. It prompts for the myQNX password (or reads
`MYQNX_USER` / `MYQNX_PASSWORD`), then lists the packages that match `qnx800`
and `qemu`; re-run with `--install` to add the baseline and the Quick Start
image.

The QNX Everywhere Quick Start Target Image ships as the
`com.qnx.qnx800.quickstart.qemu` package in QNX Software Center. Its own launch
path assumes a Linux host (KVM, bridged networking, `/dev/urandom`), so
`qemu/run-qnx.sh` boots the same image on Windows with WHPX (falling back to
TCG), user-mode networking and a builtin RNG:

```sh
./qemu/run-qnx.sh ~/qnx800/images/qemu/qemu
```

It forwards guest port 22 to host port 2222, which is the address the bridge's
`SSHTarget` expects. The VM is headless: the QNX serial console attaches to the
terminal, and `Ctrl-A x` exits.

The image's documented interface is the serial console, not SSH, so the bridge
also ships a `SerialTarget`: it starts the console command, logs in, and runs
each tool by appending an `echo` sentinel to the command line so output can be
delimited without a prompt parser.

## Status

The MCP layer and the QNX command mapping are implemented and covered by the
smoke test against a mock target. Wiring to a live QNX 8.0 instance needs the
QNX SDP 8.0 download from QNX Everywhere (free, non-commercial, requires a
myQNX account) and the QEMU image package.
