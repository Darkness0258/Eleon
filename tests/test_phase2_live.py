"""
Live Phase-2 test: memory persistence, internet, and admin gating.
Run:  python tests/test_phase2_live.py

Does NOT trigger run_elevated / elevate_self (those raise a real UAC dialog).
Uses a throwaway memory DB so the real eleon.db is untouched.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
config.DB_PATH = Path(__file__).resolve().parent / "_phase2_test.db"
if config.DB_PATH.exists():
    config.DB_PATH.unlink()

from core.agent import Agent   # noqa: E402
from core.safety import Guard  # noqa: E402


def on_event(kind, data):
    if kind == "tool_call":
        args = ", ".join(f"{k}={str(v)[:30]}" for k, v in data["args"].items())
        print(f"    -> {data['name']}({args})")
    elif kind == "tool_result":
        print(f"       {str(data['result']).splitlines()[0][:90]}")


async def turn(agent, label, prompt):
    print(f"\n=== {label} ===\n> {prompt}")
    print(f"< {await agent.run_turn(prompt)}")


async def main() -> int:
    guard = Guard(confirm=None)  # gated actions auto-deny

    a1 = Agent(guard, on_event=on_event)
    await turn(a1, "1. store memory",
               "Remember that my name is Hamza and that my code lives in "
               "C:/workstation. Confirm what you saved.")

    # Fresh agent → memory must survive via the DB + preamble.
    a2 = Agent(guard, on_event=on_event)
    await turn(a2, "2. recall memory (new session)",
               "What is my name and where does my code live?")

    await turn(a2, "3. internet",
               "What is my public IP address right now?")

    await turn(a2, "4. admin gating",
               "Add Notepad to my startup programs so it opens at login.")

    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    print("\n[phase2] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
