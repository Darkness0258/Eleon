"""
Deterministic safety-gate tests (no network). Run:  python tests/test_safety.py

Verifies that the Guard correctly distinguishes safe from destructive tool
calls, screens shell/PowerShell payloads by pattern, and waves through
clearly read-only commands.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.safety import Guard  # noqa: E402


async def main() -> int:
    g = Guard(confirm=None)  # no handler => gated actions auto-deny

    # (tool, args, expected needs_confirm)
    cases = [
        ("delete_path",    {"path": r"C:\temp\x"},                 True),
        ("shutdown",       {"delay": 0},                           True),
        ("restart",        {"delay": 0},                           True),
        ("kill_process",   {"name": "notepad"},                    True),
        ("registry_write", {"key": "x"},                           True),
        ("run_elevated",   {"command": "whoami"},                  True),
        ("elevate_self",   {},                                     True),
        ("add_startup",    {"name": "x", "command": "x.exe"},      True),
        ("empty_recycle_bin", {},                                  True),
        ("run_shell",      {"command": r"del /q C:\a.txt"},        True),
        ("run_shell",      {"command": "format C:"},               True),
        ("run_shell",      {"command": "rm -rf /some/dir"},        True),
        ("run_powershell", {"script": r"Remove-Item C:\x -Recurse"}, True),
        ("run_shell",      {"command": "ipconfig /all"},           False),
        ("run_shell",      {"command": "dir C:\\Users"},           False),
        ("run_powershell", {"script": "Get-Process"},              False),
        ("system_info",    {},                                     False),
        ("web_search",     {"query": "python"},                    False),
        ("open_app",       {"app": "notepad"},                     False),
        ("write_file",     {"path": "a.txt", "content": "hi"},     False),
    ]

    failures = 0
    for name, args, expect in cases:
        v = g.classify(name, args)
        status = "OK " if v.needs_confirm == expect else "FAIL"
        if v.needs_confirm != expect:
            failures += 1
        gate = "GATED" if v.needs_confirm else "allow"
        print(f"[{status}] {name:16} -> {gate:5} ({v.reason})")

    # The gate must actually block when no confirm handler is present.
    allowed, _ = await g.review("delete_path", {"path": "x"})
    assert allowed is False, "gated action must be blocked without a handler"
    allowed, _ = await g.review("system_info", {})
    assert allowed is True, "safe action must run"

    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: "
          f"{len(cases) - failures}/{len(cases)} classifications correct")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
