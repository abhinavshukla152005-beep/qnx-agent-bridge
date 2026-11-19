"""Protocol smoke test: drives the bridge over stdio against a target."""

from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(target_args: list[str]) -> None:
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(ROOT / "server.py"), *target_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def send(payload: dict) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    def request(request_id: int, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        send(body)
        assert process.stdout is not None
        return json.loads(process.stdout.readline())

    def tool(name: str, arguments: dict | None = None) -> dict:
        return request(99, "tools/call", {"name": name, "arguments": arguments or {}})[
            "result"
        ]

    label = " ".join(target_args)
    init = request(1, "initialize", {"protocolVersion": "2026-07-28", "capabilities": {}})
    assert init["result"]["serverInfo"]["name"] == "qnx-agent-bridge", label
    assert "tools" in init["result"]["capabilities"], label
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    tools = request(2, "tools/list")["result"]["tools"]
    names = {item["name"] for item in tools}
    assert names == {
        "qnx_list_processes",
        "qnx_process_detail",
        "qnx_system_log",
        "qnx_system_info",
    }, f"{label}: {names}"

    listing = json.loads(tool("qnx_list_processes")["content"][0]["text"])
    assert listing[0] == {
        "pid": 1,
        "name": "procnto-smp-instr",
        "state": "READY",
        "threads": 1,
        "priority": 0,
        "policy": "FIFO",
    }, f"{label}: {listing[0]}"
    assert [item["pid"] for item in listing] == sorted(item["pid"] for item in listing), label

    detail = json.loads(tool("qnx_process_detail", {"pid": 24580})["content"][0]["text"])
    assert detail["process"]["name"] == "qnx-agent-demo", f"{label}: {detail['process']}"
    assert detail["cmdline"] == ["qnx-agent-demo", "--watch", "/sensors"], f"{label}: {detail['cmdline']}"
    assert len(detail["threads"]) == 4, f"{label}: {detail['threads']}"
    assert detail["threads"][0]["blocked"] == "MsgReceive", f"{label}: {detail['threads'][0]}"
    assert detail["threads"][0]["policy"] == "RR", f"{label}: {detail['threads'][0]}"

    missing = tool("qnx_process_detail", {"pid": 4})
    assert missing["isError"] is True, label

    log = json.loads(tool("qnx_system_log", {"lines": 2})["content"][0]["text"])
    assert len(log["entries"]) == 2, f"{label}: {log}"

    warnings = json.loads(tool("qnx_system_log", {"severity": "warning"})["content"][0]["text"])
    assert len(warnings["entries"]) == 2, f"{label}: {warnings}"
    assert all(
        "WARNING" in row or "ERROR" in row for row in warnings["entries"]
    ), f"{label}: {warnings}"

    info = json.loads(tool("qnx_system_info")["content"][0]["text"])
    assert info["kernel"].startswith("QNX"), label
    assert info["process_count"] == 5, f"{label}: {info}"

    bad_pid = tool("qnx_process_detail", {"pid": 0})
    assert bad_pid["isError"] is True, label

    bad_tool = tool("qnx_rm_rf")
    assert bad_tool["isError"] is True, label

    assert process.stdin is not None
    process.stdin.close()
    process.wait(timeout=15)
    assert process.returncode == 0, f"{label}: exit {process.returncode}"


run(["--mock"])
console = " ".join(
    shlex.quote(part)
    for part in [sys.executable, str(ROOT / "tests" / "fake_qnx_serial.py")]
)
run([f"--serial={console}"])
print("smoke test passed")
