"""
eleon Brain — LLM client with native tool-calling and tiered fallback.

All three providers (OpenRouter, Groq, Ollama) speak the OpenAI
chat-completions dialect, so a single request shape works for every tier.
We simply swap base_url / api_key / model.

The brain is intentionally thin: it takes a message list + tool schemas and
returns the assistant's message (which may contain `tool_calls`). The agent
loop in core/agent.py owns the conversation state and decides what to do
with tool calls. This separation keeps the loop testable.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from config import brain_tiers, REQUEST_TIMEOUT, TEMPERATURE

# Free-tier providers rate-limit (HTTP 429) in bursts. When an entire sweep
# of tiers fails, we wait and sweep again rather than giving up — most 429s
# clear within a couple of seconds.
_MAX_ROUNDS = 3
_BACKOFF_BASE = 1.5  # seconds; grows each round


class BrainError(RuntimeError):
    """Raised when no brain tier could produce a response."""


class Brain:
    def __init__(self):
        self.tiers = brain_tiers()
        # Remember which tier last worked so we try it first next time.
        self._preferred = 0

    def _order(self) -> list[int]:
        idx = list(range(len(self.tiers)))
        # Move preferred tier to the front without dropping the others.
        idx.sort(key=lambda i: (i != self._preferred, i))
        return idx

    async def complete(self, messages: list[dict],
                       tools: list[dict] | None = None) -> dict:
        """
        Send one chat-completion request. Returns the assistant `message`
        dict (OpenAI shape): {"role": "assistant", "content": str|None,
        "tool_calls": [...]}. Sweeps every tier; if all fail (usually
        transient 429s), backs off and sweeps again up to _MAX_ROUNDS.
        """
        last_errors: list[str] = []
        for rnd in range(_MAX_ROUNDS):
            errors: list[str] = []
            for i in self._order():
                tier = self.tiers[i]
                try:
                    msg = await self._call(tier, messages, tools)
                    self._preferred = i
                    return msg
                except Exception as e:  # noqa: BLE001 — fall through
                    errors.append(f"{tier['label']}: {e}")
                    continue
            last_errors = errors
            if rnd < _MAX_ROUNDS - 1:
                await asyncio.sleep(_BACKOFF_BASE * (rnd + 1))
        raise BrainError("All brain tiers failed after "
                         f"{_MAX_ROUNDS} rounds:\n  " + "\n  ".join(last_errors))

    async def _call(self, tier: dict, messages: list[dict],
                    tools: list[dict] | None) -> dict:
        payload: dict = {
            "model": tier["model"],
            "messages": messages,
            "temperature": TEMPERATURE,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {tier['api_key']}",
            "Content-Type": "application/json",
        }
        # OpenRouter appreciates these attribution headers.
        if tier["label"].startswith("openrouter"):
            headers["HTTP-Referer"] = "https://github.com/eleon"
            headers["X-Title"] = "eleon"

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{tier['base_url']}/chat/completions",
                headers=headers, json=payload,
            )

        # OpenRouter can return HTTP 200 with an error body — check both.
        try:
            data = r.json()
        except json.JSONDecodeError:
            r.raise_for_status()
            raise RuntimeError(f"non-JSON response: {r.text[:200]}")

        if isinstance(data, dict) and data.get("error"):
            msg = data["error"]
            msg = msg.get("message", msg) if isinstance(msg, dict) else msg
            raise RuntimeError(str(msg))

        r.raise_for_status()

        choices = data.get("choices")
        if not choices:
            raise RuntimeError(f"no choices in response: {str(data)[:200]}")

        message = choices[0]["message"]
        # Normalise: guarantee the keys the agent loop expects exist.
        message.setdefault("role", "assistant")
        message.setdefault("content", "")

        # OpenAI always returns an id per tool_call, but some fallback models
        # (Ollama, older OpenRouter models) omit it. Backfill a stable id here
        # so the assistant message and its matching tool result carry the SAME
        # id — otherwise the next request would 400 on an unpaired tool call.
        for j, tc in enumerate(message.get("tool_calls") or []):
            tc.setdefault("type", "function")
            if not tc.get("id"):
                fn = tc.get("function", {})
                tc["id"] = f"call_{j}_{fn.get('name', 'tool')}"

        # Some free "reasoning" models return content=None with no tool_calls
        # (the real text is buried in a non-standard `reasoning` field). That
        # is useless to the agent loop — treat it as a tier failure so we
        # fall through to a model that actually answers. If a reasoning
        # string is present, surface it as content rather than discarding it.
        has_calls = bool(message.get("tool_calls"))
        has_text = bool((message.get("content") or "").strip())
        if not has_calls and not has_text:
            reasoning = (message.get("reasoning") or "").strip()
            if reasoning:
                message["content"] = reasoning
            else:
                raise RuntimeError("empty response (no content, no tool_calls)")
        return message

    @property
    def active_label(self) -> str:
        return self.tiers[self._preferred]["label"]
