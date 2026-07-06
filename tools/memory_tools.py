"""
eleon memory tools — let the model persist and recall facts on its own.

These wrap core/memory.py so the agent can, mid-conversation, choose to
remember something Boss said ("my projects live in C:\\work"), look it up
later, list what it knows, or forget on request. The agent also gets a
memory preamble injected at startup (see core/agent.py); these tools are for
live reads/writes during a turn.
"""
from __future__ import annotations

from core.memory import Memory
from core.tools import tool

_MEM = Memory()


@tool("remember", "Save a durable fact about Boss or this machine so eleon "
      "recalls it in future sessions.",
      {"type": "object",
       "properties": {"fact": {"type": "string",
                               "description": "the fact to store"},
                      "key": {"type": "string",
                              "description": "optional short label/topic"}},
       "required": ["fact"]})
async def remember(fact: str, key: str = "") -> str:
    saved = _MEM.remember(fact, key or None)
    return f"Remembered: {fact}" if saved else "Already knew that."


@tool("recall", "Search eleon's saved memories. Blank query returns the most "
      "recent facts.",
      {"type": "object",
       "properties": {"query": {"type": "string"}}})
async def recall(query: str = "") -> str:
    hits = _MEM.recall(query, limit=10)
    return "\n".join(f"- {h}" for h in hits) if hits else "(nothing remembered yet)"


@tool("list_memories", "List everything eleon currently remembers.",
      {"type": "object", "properties": {}})
async def list_memories() -> str:
    hits = _MEM.recall(limit=50)
    return "\n".join(f"- {h}" for h in hits) if hits else "(memory is empty)"


@tool("forget", "Delete saved memories matching a query.",
      {"type": "object",
       "properties": {"query": {"type": "string"}},
       "required": ["query"]})
async def forget(query: str) -> str:
    n = _MEM.forget(query)
    return f"Forgot {n} mem{'ory' if n == 1 else 'ories'} matching '{query}'."
