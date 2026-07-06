"""
eleon Safety — risk classification, confirmation gate, and audit log.

Every tool call passes through `Guard.review()` before execution. The guard:
  1. Classifies the call as SAFE or NEEDS_CONFIRM.
  2. For NEEDS_CONFIRM, asks the user (via an injected confirm callback).
  3. Writes an append-only audit record either way.

Two layers of screening:
  - Name-based: tools listed in config.CONFIRM_TOOLS always confirm.
  - Pattern-based: shell / powershell / registry arguments are scanned for
    destructive patterns (del, format, rmdir, rm -rf, reg delete, etc.) so
    an innocuously named `run_shell` can't smuggle a `format C:` past us.

The confirm callback is supplied by the interface layer (CLI now, GUI later)
so this module stays UI-agnostic and unit-testable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from config import CONFIRM_TOOLS, LOG_DIR, AUDIT_TO_CONSOLE

# Patterns that make ANY shell/registry payload destructive.
_DANGER_PATTERNS = [
    r"\bformat\b",                       # format a drive
    r"\bdel\b", r"\bdelete\b", r"\berase\b",
    r"\brmdir\b", r"\brd\b\s+/s",        # recursive dir removal
    r"rm\s+-rf?", r"rm\s+-fr?",          # unix-style recursive delete
    r"\bremove-item\b",                  # PowerShell delete
    r"reg\s+delete", r"remove-itemproperty",
    r"\bshutdown\b", r"\brestart-computer\b",
    r"\bdiskpart\b", r"\bcipher\b\s+/w", # secure wipe
    r"\bmklink\b",                       # symlink games
    r"set-executionpolicy",
    r"\bnet\s+user\b.*\/add",            # add a user account
    r"\bnetsh\b.*\bfirewall\b",          # firewall changes
    r"takeown|icacls",                   # ownership / ACL changes
    r">\s*[A-Za-z]:\\",                  # redirect overwrite to a drive path
    r"\bformat-volume\b",
]
_DANGER_RE = re.compile("|".join(_DANGER_PATTERNS), re.IGNORECASE)

# Arguments in these tool fields carry the "command" to screen.
_COMMAND_FIELDS = ("command", "script", "code", "cmd")


@dataclass
class Verdict:
    needs_confirm: bool
    reason: str


class Guard:
    def __init__(self,
                 confirm: Callable[[str, dict, str], Awaitable[bool]] | None = None):
        """
        confirm: async callback (tool_name, args, reason) -> bool.
                 Returns True to proceed, False to cancel. If None, any call
                 needing confirmation is auto-denied (safe default).
        """
        self._confirm = confirm
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._audit_path = LOG_DIR / "audit.log"

    # ── Classification ─────────────────────────────────────────────
    def classify(self, tool_name: str, args: dict) -> Verdict:
        if tool_name in CONFIRM_TOOLS:
            # Shell/PS tools are gated by name, but a clearly read-only
            # command can be waved through to keep the assistant fluid.
            if tool_name in ("run_shell", "run_powershell"):
                blob = self._command_blob(args)
                if blob and not self._looks_destructive(blob) \
                        and self._looks_readonly(blob):
                    return Verdict(False, "read-only shell command")
            return Verdict(True, f"'{tool_name}' is a gated action")

        blob = self._command_blob(args)
        if blob and self._looks_destructive(blob):
            return Verdict(True, "arguments match a destructive pattern")

        return Verdict(False, "safe")

    def _command_blob(self, args: dict) -> str:
        parts = [str(args.get(f, "")) for f in _COMMAND_FIELDS]
        return " ".join(p for p in parts if p).strip()

    def _looks_destructive(self, blob: str) -> bool:
        return bool(_DANGER_RE.search(blob))

    def _looks_readonly(self, blob: str) -> bool:
        # Exact-match verbs (whole first token must equal one of these).
        readonly_exact = {
            "dir", "ls", "type", "cat", "echo", "whoami", "hostname",
            "ipconfig", "ping", "systeminfo", "tasklist", "where",
            "ver", "date", "time", "vol", "tree", "findstr", "wmic",
        }
        # Prefix verbs (e.g. PowerShell Get-Process, Get-ChildItem).
        readonly_prefixes = ("get-",)
        tokens = blob.lower().lstrip("(& ").split()
        if not tokens:
            return False
        head = tokens[0]
        return head in readonly_exact or head.startswith(readonly_prefixes)

    # ── The gate ───────────────────────────────────────────────────
    async def review(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """
        Returns (allowed, reason). Logs the decision. If confirmation is
        needed, invokes the confirm callback.
        """
        verdict = self.classify(tool_name, args)

        if not verdict.needs_confirm:
            self._audit("ALLOW", tool_name, args, verdict.reason)
            return True, verdict.reason

        if self._confirm is None:
            self._audit("DENY", tool_name, args, "no confirm handler")
            return False, "confirmation required but no handler available"

        approved = await self._confirm(tool_name, args, verdict.reason)
        self._audit("CONFIRM_OK" if approved else "CONFIRM_NO",
                    tool_name, args, verdict.reason)
        return approved, ("approved by user" if approved
                          else "cancelled by user")

    # ── Audit log ──────────────────────────────────────────────────
    def log_result(self, tool_name: str, args: dict, result: str):
        self._audit("RESULT", tool_name, args, str(result)[:500])

    def _audit(self, event: str, tool_name: str, args: dict, note: str):
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "tool": tool_name,
            "args": args,
            "note": note,
        }
        line = json.dumps(rec, ensure_ascii=False, default=str)
        try:
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass  # auditing must never crash the assistant
        if AUDIT_TO_CONSOLE and event in ("CONFIRM_OK", "CONFIRM_NO", "DENY"):
            print(f"  [audit] {event}: {tool_name} — {note}")
