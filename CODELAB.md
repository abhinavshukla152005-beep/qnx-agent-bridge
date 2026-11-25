# Codelab: give an AI agent read-only eyes on a QNX 8.0 target

**Time:** 45–60 minutes · **Level:** intermediate · **Cost:** ₹0 / $0

You will run an MCP server, call it by hand, wire it into a real agent, point
it at a real QNX target, and add a tool of your own.

Modules 0–2 and 5 need **no QNX licence and no target hardware** — the server
ships a mock target, so you can finish most of this codelab on any laptop.
Module 4 is the only part that needs the QNX Software Development Platform.

Everything here is verified by `python tools/codelab_check.py`, which runs the
same commands this document asks you to run and compares the output.

---

## What you end up with

```
agent  --stdio JSON-RPC-->  server.py  --ssh-->  QNX 8.0 target
                                                pidin / slog2info / procfs
```

Four read-only tools (five after Module 5):

| Tool | Answers |
| --- | --- |
| `qnx_list_processes` | "What is running, at what priority and policy?" |
| `qnx_process_detail` | "Why is this process in this state, and what are its threads doing?" |
| `qnx_system_log` | "What did the system log say at warning or worse?" |
| `qnx_system_info` | "Which QNX build is this, and how big is the process table?" |

---

## Module 0 — Get it running (5 min)

**Goal:** prove the server speaks MCP before you point it at anything real.

```sh
git clone https://github.com/abhinavshukla152005-beep/qnx-agent-bridge
cd qnx-agent-bridge
python tests/smoke.py
```

Works on Python 3.10+. No dependencies, no pip install.

**You should see:**

```
smoke test passed
```

That one line means the server completed a full MCP handshake twice: once
against the mock target and once against a scripted QNX console
(`tests/fake_qnx_serial.py`), including the login prompt path.

**Why it works:** `server.py` reads newline-delimited JSON from stdin and
writes one JSON line per request. `transport.py` owns everything that touches
the target, so the protocol layer never knows whether it is talking to a mock,
an SSH socket or a serial console.

---

## Module 1 — Talk MCP by hand (10 min)

**Goal:** see the wire format. An agent does nothing you cannot do with a
pipe, which is exactly why this is debuggable.

### 1.1 Handshake

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{}}}' \
  | python server.py --mock
```

**You should see:**

```json
{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2026-07-28", "capabilities": {"tools": {"listChanged": false}}, "serverInfo": {"name": "qnx-agent-bridge", "version": "0.1.0"}}}
```

### 1.2 Discover the tools

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python server.py --mock
```

**You should see** four entries with `name`, `description` and `inputSchema`.
The description is not decoration: it is the only thing the model reads when
deciding whether to call your tool.

### 1.3 Call one

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"qnx_list_processes","arguments":{}}}' \
  | python server.py --mock
```

**You should see** a JSON array of processes, sorted by pid:

```json
[
  {"pid": 1, "name": "procnto-smp-instr", "state": "READY", "threads": 1, "priority": 0, "policy": "FIFO"},
  {"pid": 8194, "name": "slogger2", "state": "RECEIVE", "threads": 3, "priority": 10, "policy": "FIFO"},
  ...
]
```

### 1.4 Fail on purpose

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"qnx_process_detail","arguments":{"pid":999999}}}' \
  | python server.py --mock
```

**You should see** `"isError": true` with the message `no process with pid
999999`. Errors come back as tool results, not crashes, so an agent can read
them and try something else.

> **On Windows PowerShell** the quoting above is painful. Use Git Bash, WSL,
> or skip to Module 2 and let the agent do the talking.

---

## Module 2 — Plug it into a real agent (10 min)

**Goal:** get the four tools in front of a model in a real client.

Pick one. All of them launch the same command:

```sh
python /absolute/path/to/qnx-agent-bridge/server.py --mock
```

### Claude Desktop

Edit `claude_desktop_config.json`
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/` on macOS):

```json
{
  "mcpServers": {
    "qnx-agent-bridge": {
      "command": "python",
      "args": ["C:\\tools\\qnx-agent-bridge\\server.py", "--mock"]
    }
  }
}
```

Restart the app. The tools appear under the MCP/connectors indicator.

### Cursor

`.cursor/mcp.json` in the project:

```json
{
  "mcpServers": {
    "qnx-agent-bridge": {
      "command": "python",
      "args": ["${workspaceFolder}/server.py", "--mock"]
    }
  }
}
```

### VS Code (GitHub Copilot agent mode)

`.vscode/mcp.json` — note the different top-level key:

```json
{
  "servers": {
    "qnx-agent-bridge": {
      "type": "stdio",
      "command": "python",
      "args": ["${workspaceFolder}/server.py", "--mock"]
    }
  }
}
```

**Then ask, in plain English:**

> What is running on the target, and which thread missed its deadline?

**You should see** the agent call `qnx_system_info`, then `qnx_list_processes`,
then `qnx_system_log` with `severity: "warning"`, and answer with the
`WARNING agent-demo: thread 3 blocked on MsgReceive` and `ERROR agent-demo:
watchdog deadline missed` lines quoted from the log.

If the tools never appear, run the command from Module 2 by hand first; a
server that dies on startup shows up in the client as an empty tool list, not
as an error.

---

## Module 3 — Read the scheduling fields correctly (10 min)

**Goal:** stop mis-reading `pidin`, which is where most "the target is slow"
debugging goes wrong.

`pidin` prints priority and scheduling policy as one field. On this target the
demo process shows `16r`, which is **priority 16, round-robin**. The bridge
splits it:

```json
{"pid": 24580, "priority": 16, "policy": "RR"}
```

| Suffix | Policy | What it means for latency |
| --- | --- | --- |
| `f` | FIFO | Same priority never yields; a busy FIFO thread starves its peers. |
| `r` | RR | Same priority time-slices; a busy thread cannot lock out its peers. |
| `s` | SPORADIC | Time-partitioned, for hard real-time budgets. |

**Exercise:** from Module 1.3 output, answer two questions without opening a
QNX box:

1. Which process has the highest priority?
2. `io-pkt-v6-hc` runs at priority 21 FIFO while `qnx-agent-demo` runs at 16
   RR. If the network stack saturates, what happens to the demo's deadline?

Question 2 has a real answer: a FIFO thread at 21 cannot be preempted by a
round-robin thread at 16, so the demo's watchdog can miss while the network
thread is runnable. That is the class of bug this bridge exists to surface —
and the reason the `blocked on` field matters as much as the priority.

**You should see** why the tool returns `blocked` per thread: `MsgReceive`,
`nanosleep` and `""` (runnable) tell you different stories at the same
priority.

---

## Module 4 — Point it at a real QNX target (15 min)

**Goal:** run the same tools against QNX 8.0 instead of the mock.

You need the QNX SDP 8.0 image, which is free for non-commercial use through
**QNX Everywhere** (`qnx.software/en/developers/get-started/qnx-everywhere`).
A myQNX account is required.

### 4.1 Install the toolchain (Windows)

```sh
bash qemu/install-sdp.sh            # lists the matching packages
bash qemu/install-sdp.sh --install  # adds the baseline + Quick Start image
```

The script prompts for your myQNX password, or reads `MYQNX_PASSWORD`. It
defaults its workspace to `C:\qnxws`, because the QNX CLI rejects paths with
spaces and `C:\Users\<you>` has one.

### 4.2 Boot the target

The Quick Start image assumes a Linux host (KVM, bridged networking,
`/dev/urandom`). `qemu/run-qnx.sh` boots the same image on Windows with WHPX
(falling back to TCG), user-mode networking and a builtin RNG:

```sh
./qemu/run-qnx.sh ~/qnx800/images/qemu/qemu
```

**You should see** the QNX boot banner and a `login:` prompt on the serial
console. `Ctrl-A x` exits QEMU.

### 4.3 Switch the bridge to the live console

```sh
python server.py --serial="bash qemu/run-qnx.sh ~/qnx800/images/qemu/qemu"
```

Then repeat Module 1.3. Same JSON, real values.

**Why serial and not SSH:** the Quick Start image documents the serial console
as its interface and ships no `sshd`. `SerialTarget` logs in, appends
`echo <sentinel>` to each command so output is delimited without guessing at
`# ` prompts, and copes with `/proc/<pid>/cmdline` being NUL-separated with no
trailing newline.

If your image *does* run sshd, use `--host` instead and skip the console:

```sh
python server.py --host=127.0.0.1 --port=2222 --user=root
```

### 4.4 Confirm the deadline story on real hardware

```sh
python tests/demo_client.py --serial="bash qemu/run-qnx.sh ~/qnx800/images/qemu/qemu"
```

**You should see** the same summary as the README, but with the guest's real
process table. Compare `qnx_system_log` against `slog2info -w` in a second
console: the bridge does not stream, it dumps.

---

## Module 5 — Add your own tool (15 min)

**Goal:** the bridge is a pattern, not a fixed set of four tools. Add
`qnx_find_process`, which answers "where is the process called …?" without
piping `pidin` into `grep`.

### 5.1 Declare the tool

In `server.py`, add this entry to `TOOLS`, before the `qnx_system_info` entry:

```python
    {
        "name": "qnx_find_process",
        "description": "Find processes whose name contains a substring, with pid, state, priority and policy.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
```

### 5.2 Implement it

Still in `server.py`, add this branch to `call_tool`, before the
`qnx_system_info` branch:

```python
    if name == "qnx_find_process":
        needle = arguments.get("name")
        if not isinstance(needle, str) or not needle.strip():
            msg = "name must be a non-empty string"
            raise ValueError(msg)
        matches = [
            item
            for item in parse_pidin(target.run(["pidin", "-F", PIDIN_FORMAT]))
            if needle.lower() in item["name"].lower()
        ]
        return _text({"query": needle, "matches": matches, "count": len(matches)})
```

Nothing new touches the target: it reuses `pidin` and the existing parser, so
the read-only guarantee is unchanged.

### 5.3 Update the test

`tests/smoke.py` asserts the exact tool set, which is intentional: a tool that
silently disappears is a bug. Add the name:

```python
        "qnx_system_info",
        "qnx_find_process",
    }, f"{label}: {names}"
```

and add one behavioural assertion after the `qnx_system_info` check:

```python
    found = json.loads(tool("qnx_find_process", {"name": "slog"})["content"][0]["text"])
    assert found["count"] == 1 and found["matches"][0]["pid"] == 8194, f"{label}: {found}"
```

### 5.4 Verify

```sh
python tests/smoke.py
```

**You should see** `smoke test passed` again — now covering five tools on both
transports.

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"qnx_find_process","arguments":{"name":"slog"}}}' \
  | python server.py --mock
```

**You should see:**

```json
{
  "query": "slog",
  "matches": [
    {"pid": 8194, "name": "slogger2", "state": "RECEIVE", "threads": 3, "priority": 10, "policy": "FIFO"}
  ],
  "count": 1
}
```

Search is case-insensitive, so `"DEMO"` also matches `qnx-agent-demo`, and an
unknown name returns `"count": 0` rather than an error.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `console closed while waiting for '# '` | QEMU exited; usually a missing image path | Re-run `qemu/run-qnx.sh` and check the image directory |
| `timed out waiting for "__QNXBRIDGE_…"` | Command hung — usually `slog2info -w` | Use `slog2info` without `-w`; filter severity in the bridge |
| `Cannot read the array length because "allrepos" is null` | myQNX account has no SDP 8.0 key deployed | Accept the QNX Everywhere licence, then **Register a Product** |
| Tools list is empty in Claude/Cursor | Server crashed at startup | Run the exact command from the config in a terminal |
| `mock target has no response for 'x'` | You added a tool that calls a new utility | Add a response to `MOCK_RESPONSES` in `transport.py` |

---

## What to try next

- Add `qnx_priority_inversion`: list threads blocked on a `MsgSend` to a
  *lower*-priority process. The data is already in `pidin -F "%a|%b|%N|%J|%B|%p"`.
- Add `qnx_memory`: `pidin -F "%a|%N|%m"`, and compare against `/proc/<pid>/asinfo`.
- Return `slog2info -t` timestamps as epoch seconds so an agent can order
  events without parsing locale-dependent dates.

Every extension follows the same three steps as Module 5: declare, implement,
test.
