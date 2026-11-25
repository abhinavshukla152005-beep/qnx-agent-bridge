#!/usr/bin/env python3
"""Verify the claims made in CODELAB.md, end to end.

    python tools/codelab_check.py

Runs the same stdio session the codelab asks a reader to run, plus the
smoke test, and prints one line per check. Exit code 0 means the codelab
still matches the code.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE_TOOLS = {"qnx_list_processes", "qnx_process_detail", "qnx_system_log", "qnx_system_info"}


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'' if ok or not detail else f' :: {detail}'}")
    return ok


def main() -> int:
    failures = 0

    smoke = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "smoke.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    failures += not check(
        "Module 0: smoke test passes",
        smoke.returncode == 0 and "smoke test passed" in smoke.stdout,
        (smoke.stdout + smoke.stderr).strip()[-300:],
    )

    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(ROOT / "server.py"), "--mock"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None and process.stdout is not None

    def request(request_id: int, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        process.stdin.write(json.dumps(body) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())

    def call(name: str, arguments: dict | None = None) -> dict:
        result = request(9, "tools/call", {"name": name, "arguments": arguments or {}})["result"]
        return {"__error__": json.loads(result["content"][0]["text"])} if result.get("isError") else json.loads(result["content"][0]["text"])

    init = request(1, "initialize", {"protocolVersion": "2026-07-28", "capabilities": {}})
    failures += not check(
        "Module 1.1: initialize returns the server identity",
        init["result"]["serverInfo"]["name"] == "qnx-agent-bridge"
        and init["result"]["protocolVersion"] == "2026-07-28",
        json.dumps(init["result"].get("serverInfo")),
    )

    names = {item["name"] for item in request(2, "tools/list")["result"]["tools"]}
    failures += not check(
        "Module 1.2: the four documented tools are listed",
        BASE_TOOLS <= names,
        f"missing {sorted(BASE_TOOLS - names)}",
    )

    listing = call("qnx_list_processes")
    failures += not check(
        "Module 1.3: qnx_list_processes returns sorted process rows",
        isinstance(listing, list)
        and listing[0]["pid"] == 1
        and listing[0]["name"] == "procnto-smp-instr"
        and [row["pid"] for row in listing] == sorted(row["pid"] for row in listing),
        str(listing)[:200],
    )

    missing = call("qnx_process_detail", {"pid": 999999})
    failures += not check(
        "Module 1.4: an unknown pid is an isError result, not a crash",
        "__error__" in missing and "no process with pid" in str(missing),
        str(missing)[:200],
    )

    detail = call("qnx_process_detail", {"pid": 24580})
    failures += not check(
        "Module 3: priority and policy are split from pidin's %p field",
        detail["process"]["priority"] == 16 and detail["process"]["policy"] == "RR",
        json.dumps(detail.get("process")),
    )

    demo = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "demo_client.py"), "--mock"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    failures += not check(
        "Module 4.4: the demo client renders the documented summary",
        demo.returncode == 0
        and "qnx-agent-demo (pid 24580)" in demo.stdout
        and "system log (2 at warning or worse)" in demo.stdout,
        (demo.stdout + demo.stderr).strip()[-300:],
    )

    if len(names) > len(BASE_TOOLS):
        found = call("qnx_find_process", {"name": "slog"})
        failures += not check(
            "Module 5: the extension tool answers by name",
            found.get("count") == 1 and found["matches"][0]["pid"] == 8194,
            str(found)[:200],
        )

    process.stdin.close()
    process.wait(timeout=15)
    print()
    print("codelab check passed" if not failures else f"{failures} check(s) failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
