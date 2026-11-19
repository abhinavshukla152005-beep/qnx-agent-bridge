#!/usr/bin/env python3
"""Drive the bridge the way an agent would and print a readable summary.

    python tests/demo_client.py --mock
    python tests/demo_client.py --serial='bash qemu/run-qnx.sh ~/qnx800/images/qemu/qemu'
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main(target_args: list[str]) -> None:
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(ROOT / "server.py"), *target_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def request(request_id: int, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(body) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())

    def call(name: str, arguments: dict | None = None) -> dict:
        result = request(9, "tools/call", {"name": name, "arguments": arguments or {}})["result"]
        text = result["content"][0]["text"]
        return {"error": text} if result.get("isError") else json.loads(text)

    request(1, "initialize", {"protocolVersion": "2026-07-28", "capabilities": {}})
    assert process.stdin is not None
    process.stdin.write(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    )
    process.stdin.flush()

    info = call("qnx_system_info")
    if "error" in info:
        raise SystemExit(info["error"])
    print(f"target      {info['kernel']}")
    print(f"processes   {info['process_count']}")

    processes = call("qnx_list_processes")
    if isinstance(processes, dict) and "error" in processes:
        raise SystemExit(processes["error"])

    print("\nprocess table")
    print(f"  {'pid':>7}  {'threads':>7}  {'prio':>4}  {'policy':<7} {'state':<10} name")
    for item in processes:
        print(
            f"  {item['pid']:>7}  {item['threads']:>7}  {str(item['priority']):>4}  "
            f"{str(item['policy']):<7} {item['state']:<10} {item['name']}"
        )

    busiest = max(processes, key=lambda item: item["threads"])
    detail = call("qnx_process_detail", {"pid": busiest["pid"]})
    if "error" in detail:
        raise SystemExit(detail["error"])

    print(f"\n{detail['process']['name']} (pid {detail['process']['pid']})")
    print(f"  argv      {' '.join(detail['cmdline'])}")
    for thread in detail["threads"]:
        blocked = f" blocked on {thread['blocked']}" if thread["blocked"] else ""
        print(
            f"  thread {thread['tid']}: {thread['state']}{blocked} "
            f"prio {thread['priority']} {thread['policy']}"
        )

    log = call("qnx_system_log", {"severity": "warning"})
    if "error" in log:
        raise SystemExit(log["error"])
    print(f"\nsystem log ({len(log['entries'])} at warning or worse)")
    for entry in log["entries"]:
        print(f"  {entry}")

    process.stdin.close()
    process.wait(timeout=15)


if __name__ == "__main__":
    main(sys.argv[1:] or ["--mock"])
