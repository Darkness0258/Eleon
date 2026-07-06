"""
eleon Tool registry.

A tool is a plain async function decorated with @tool(...) that carries:
  - a name (what the model calls),
  - a description (what the model reads to decide),
  - a JSON-schema parameter spec (what arguments are valid).

The registry produces two things:
  - `openai_schema()` — the `tools` array sent to the LLM.
  - `dispatch(name, args)` — invoke the implementation by name.

Implementations live in the `tools/` package (os_tools, shell_tools,
web_tools). They import `tool` from here and register themselves at import
time. `load_all()` imports those modules so their decorators run.

Every tool returns a STRING (the observation fed back to the model). Keeping
the contract to "str in JSON args → str result" makes the agent loop and the
audit log uniform and predictable.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict            # JSON Schema (object)
    func: Callable[..., Awaitable[str]]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Registry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, t: Tool):
        if t.name in self._tools:
            raise ValueError(f"duplicate tool name: {t.name}")
        self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def openai_schema(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    async def dispatch(self, name: str, args: dict) -> str:
        t = self._tools.get(name)
        if t is None:
            return f"[error] unknown tool: {name}"
        try:
            # Only pass args the function actually accepts; ignore extras
            # the model may hallucinate.
            sig = inspect.signature(t.func)
            accepted = {k: v for k, v in (args or {}).items()
                        if k in sig.parameters}
            result = await t.func(**accepted)
            return str(result)
        except TypeError as e:
            return f"[error] bad arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001
            return f"[error] {name} failed: {e}"


# Global registry + decorator ---------------------------------------
REGISTRY = Registry()


def tool(name: str, description: str,
         parameters: dict | None = None):
    """Decorator to register an async function as a tool."""
    schema = parameters or {"type": "object", "properties": {}}

    def wrap(func: Callable[..., Awaitable[str]]):
        REGISTRY.register(Tool(name, description, schema, func))
        return func

    return wrap


def load_all():
    """Import tool modules so their @tool decorators run. Idempotent."""
    # Imported for side effects (registration).
    from tools import (os_tools, shell_tools, web_tools,  # noqa: F401
                       admin_tools, win_tools, memory_tools)
    return REGISTRY
