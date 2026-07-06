"""
eleon — entry point.

Default: the modern PyQt6 desktop GUI with voice already listening.

Usage:
    python run.py             # desktop GUI + always-on voice (default)
    python run.py --text      # interactive text chat in the terminal
    python run.py --gui       # desktop GUI (explicit; same as default)
    python run.py --voice     # terminal-only hands-free voice loop
    python run.py --voice --always  # every utterance is a command (no wake word)
    python run.py --selftest  # non-interactive loop test (no destructive ops)

If PyQt6 isn't available, the default falls back to the text CLI.
"""
from __future__ import annotations

import asyncio
import sys

from config import ASSISTANT_NAME, USER_NAME
from core.agent import Agent
from core.safety import Guard

# Windows consoles often default to cp1252, which can't encode the arrows /
# glyphs we print. Force UTF-8 on the streams; fall back silently if the
# runtime doesn't support reconfigure.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Terminal front-end ─────────────────────────────────────────────
class Colors:
    DIM = "\033[2m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def _print_event(kind: str, data: dict):
    c = Colors
    if kind == "tool_call":
        print(f"{c.DIM}  → {data['name']}({_fmt_args(data['args'])}){c.RESET}")
    elif kind == "tool_result":
        preview = str(data["result"]).replace("\n", " ")[:120]
        print(f"{c.DIM}    {preview}{c.RESET}")
    elif kind == "maxsteps":
        print(f"{c.YELLOW}  (step limit reached — wrapping up){c.RESET}")


def _fmt_args(args: dict) -> str:
    parts = []
    for k, v in (args or {}).items():
        s = str(v)
        parts.append(f"{k}={s[:40]}")
    return ", ".join(parts)


async def _confirm(tool_name: str, args: dict, reason: str) -> bool:
    """Ask the user before a gated/destructive action runs."""
    c = Colors
    print(f"\n{c.YELLOW}{c.BOLD}⚠  Confirm:{c.RESET} "
          f"{c.YELLOW}{tool_name}{c.RESET} — {reason}")
    print(f"{c.DIM}   args: {_fmt_args(args)}{c.RESET}")
    # input() is blocking; run it off the event loop.
    ans = await asyncio.to_thread(input, f"   Proceed? [y/N] ")
    return ans.strip().lower() in ("y", "yes")


def _banner():
    c = Colors
    print(f"{c.CYAN}{c.BOLD}{ASSISTANT_NAME}{c.RESET} "
          f"{c.DIM}— your desktop agent. Type 'quit' to exit, "
          f"'reset' to clear context.{c.RESET}\n")


async def chat():
    _banner()
    guard = Guard(confirm=_confirm)
    agent = Agent(guard, on_event=_print_event)
    c = Colors

    while True:
        try:
            user = (await asyncio.to_thread(input, f"{c.GREEN}You:{c.RESET} ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{ASSISTANT_NAME}: later, {USER_NAME}.")
            return
        if not user:
            continue
        low = user.lower()
        if low in ("quit", "exit"):
            print(f"{ASSISTANT_NAME}: later, {USER_NAME}.")
            return
        if low == "reset":
            agent.reset()
            print(f"{c.DIM}(context cleared){c.RESET}\n")
            continue
        try:
            reply = await agent.run_turn(user)
        except Exception as e:  # noqa: BLE001
            print(f"{c.RED}[error] {e}{c.RESET}\n")
            continue
        print(f"{c.CYAN}{ASSISTANT_NAME}:{c.RESET} {reply}\n")


# ── Self-test: exercise the loop without any destructive path ──────
async def selftest():
    print("[selftest] loading tools + registry...")
    guard = Guard(confirm=None)  # deny anything that would need confirmation
    agent = Agent(guard, on_event=_print_event)
    print(f"[selftest] {len(agent.registry.names())} tools registered:")
    print("           " + ", ".join(agent.registry.names()))

    prompt = ("Report my current CPU/RAM/battery using the system_info tool, "
              "then tell me the result in one sentence.")
    print(f"\n[selftest] turn: {prompt}\n")
    reply = await agent.run_turn(prompt)
    print(f"\n[selftest] final reply:\n{reply}")
    print("\n[selftest] OK")


def _launch_gui() -> bool:
    """Launch the desktop GUI. Returns False if PyQt6 is unavailable."""
    try:
        from ui.gui import launch
    except Exception as e:  # noqa: BLE001
        print(f"[gui] PyQt6 not available ({e}).")
        return False
    raise SystemExit(launch())


def main():
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    elif "--text" in sys.argv:
        asyncio.run(chat())
    elif "--voice" in sys.argv:
        try:
            from voice.loop import voice_chat
        except Exception as e:  # noqa: BLE001
            print(f"[voice] voice deps unavailable ({e}). Install them with:\n"
                  "      python -m pip install sounddevice edge-tts\n"
                  "      (or run the text CLI: python run.py --text)")
            return
        voice_chat(always="--always" in sys.argv)
    else:
        # Default (and --gui): the desktop GUI with always-on voice. Fall back
        # to the text CLI if PyQt6 can't load.
        if not _launch_gui():
            print("[eleon] Falling back to the text CLI "
                  "(install PyQt6 for the GUI).\n")
            asyncio.run(chat())


if __name__ == "__main__":
    main()
