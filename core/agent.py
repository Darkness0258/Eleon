"""
eleon Agent — the think → call tool → observe → repeat loop.

This is the core that makes eleon "do anything". Each user turn:

  1. Append the user message to the running conversation.
  2. Ask the brain for the next step, handing it the full tool schema.
  3. If the brain returns tool_calls:
        - run each through the safety Guard (confirm gate + audit),
        - append each result as a `tool` message,
        - loop back to step 2 so the brain can react to the results.
  4. If the brain returns plain content (no tool calls): that's the final
     answer for this turn. Return it.

A MAX_STEPS guard bounds the loop so a confused model can't spin forever.

The loop is UI-agnostic: `on_event` (optional) streams progress to whatever
front-end is attached (CLI now, GUI later), and the Guard's confirm callback
is likewise injected by the caller.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from config import SYSTEM_PROMPT, MAX_STEPS
from core.brain import Brain
from core.memory import Memory
from core.safety import Guard
from core.tools import load_all


class Agent:
    def __init__(self, guard: Guard,
                 on_event: Callable[[str, dict], None] | None = None):
        self.brain = Brain()
        self.registry = load_all()
        self.guard = guard
        self.on_event = on_event or (lambda kind, data: None)
        self.memory = Memory()
        self.messages: list[dict] = [
            {"role": "system", "content": self._system_content()}
        ]

    def _system_content(self) -> str:
        """System prompt with the memory preamble folded in, so eleon starts
        each session already knowing Boss's facts and recent history."""
        ctx = self.memory.context_block()
        return SYSTEM_PROMPT + (f"\n\n{ctx}" if ctx else "")

    def _emit(self, kind: str, **data):
        self.on_event(kind, data)

    async def run_turn(self, user_input: str) -> str:
        """Process one user message to completion; return the final reply."""
        self.messages.append({"role": "user", "content": user_input})
        self.memory.log_turn("user", user_input)
        tool_schema = self.registry.openai_schema()

        for step in range(1, MAX_STEPS + 1):
            self._emit("thinking", step=step)
            message = await self.brain.complete(self.messages, tool_schema)

            tool_calls = message.get("tool_calls") or []

            # Persist the assistant turn exactly as returned (the API
            # requires the assistant message with tool_calls to precede the
            # matching tool results).
            self.messages.append(self._clean_assistant(message))

            if not tool_calls:
                reply = (message.get("content") or "").strip() or "(no response)"
                self.memory.log_turn("assistant", reply)
                self._emit("final", text=reply)
                return reply

            # Execute every requested tool call, in order.
            for call in tool_calls:
                await self._handle_tool_call(call)

        # Ran out of steps — ask for a wrap-up without more tools.
        self._emit("maxsteps", steps=MAX_STEPS)
        self.messages.append({
            "role": "user",
            "content": ("[system] Step budget reached. Summarise what you "
                        "accomplished and what remains, without calling tools.")
        })
        message = await self.brain.complete(self.messages, tools=None)
        self.messages.append(self._clean_assistant(message))
        final = (message.get("content") or "").strip() or "(stopped: step limit)"
        self.memory.log_turn("assistant", final)
        return final

    async def _handle_tool_call(self, call: dict):
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}

        self._emit("tool_call", name=name, args=args)

        allowed, reason = await self.guard.review(name, args)
        if not allowed:
            result = f"[blocked] {reason}. Tool not executed."
        else:
            result = await self.registry.dispatch(name, args)
            self.guard.log_result(name, args, result)

        self._emit("tool_result", name=name, result=result)

        self.messages.append({
            "role": "tool",
            "tool_call_id": call.get("id", name),
            "name": name,
            "content": result,
        })

    @staticmethod
    def _clean_assistant(message: dict) -> dict:
        """Keep only the fields the chat API accepts on an assistant turn."""
        out: dict = {"role": "assistant",
                     "content": message.get("content") or ""}
        if message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        return out

    def reset(self):
        self.messages = [{"role": "system", "content": self._system_content()}]
