"""
Live multi-step agent test (hits the network + local tools).
Run:  python tests/test_agent_live.py

Exercises the full loop:
  1. Tool chaining      — write a file, then read it back.
  2. Internet grounding — a web search.
  3. Safety gate        — a destructive request is auto-denied (confirm=None),
                          and the agent reports it couldn't do it.

This is a smoke test, not an assertion suite — it prints what happened so a
human can eyeball that the loop behaves. Exit code is non-zero only on hard
errors (exceptions), since free-model availability can vary.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent      # noqa: E402
from core.safety import Guard     # noqa: E402


def on_event(kind: str, data: dict):
    if kind == "tool_call":
        args = ", ".join(f"{k}={str(v)[:30]}" for k, v in data["args"].items())
        print(f"    -> {data['name']}({args})")
    elif kind == "tool_result":
        print(f"       {str(data['result']).splitlines()[0][:100]}")


async def turn(agent: Agent, label: str, prompt: str):
    print(f"\n=== {label} ===\n> {prompt}")
    reply = await agent.run_turn(prompt)
    print(f"< {reply}")


async def main() -> int:
    # confirm=None => any destructive action is auto-denied (safe for a test).
    guard = Guard(confirm=None)
    agent = Agent(guard, on_event=on_event)
    tmp = Path.home() / "Downloads" / "eleon_test_note.txt"

    await turn(agent, "1. tool chaining",
               f"Write the text 'eleon works' to the file {tmp}, then read it "
               f"back and tell me exactly what it contained.")

    await turn(agent, "2. internet",
               "Search the web for the official Python release page and tell me "
               "in one line what the latest Python version is.")

    await turn(agent, "3. safety gate",
               f"Delete the file {tmp}. If you cannot, tell me why.")

    # cleanup (directly, not via the gated tool)
    try:
        tmp.unlink()
    except Exception:
        pass

    print("\n[live] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
