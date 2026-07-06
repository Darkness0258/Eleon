"""
eleon admin tools — elevation, registry, and startup persistence.

These reach the privileged parts of Windows. The sensitive members
(run_elevated, elevate_self, registry_write, add_startup) are gated by
core/safety.py (listed in config.CONFIRM_TOOLS), so Boss confirms before any
of them run. Read-only members (check_admin, registry_read) run freely.
"""
from __future__ import annotations

import winreg

from core.elevate import is_admin, relaunch_as_admin, run_elevated
from core.tools import tool

_HIVES = {
    "HKCU": winreg.HKEY_CURRENT_USER,  "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
    "HKLM": winreg.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
    "HKCR": winreg.HKEY_CLASSES_ROOT,  "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
    "HKU":  winreg.HKEY_USERS,         "HKEY_USERS": winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _split(path: str):
    """'HKCU\\Software\\X' → (hive_handle, 'Software\\X')."""
    hive, _, sub = path.replace("/", "\\").partition("\\")
    h = _HIVES.get(hive.upper())
    if h is None:
        raise ValueError(f"unknown registry hive: {hive}")
    return h, sub


# ── Elevation ──────────────────────────────────────────────────────
@tool("check_admin", "Report whether eleon is currently running with "
      "Administrator privileges.",
      {"type": "object", "properties": {}})
async def check_admin() -> str:
    return ("eleon IS running as Administrator (elevated)." if is_admin()
            else "eleon is running as a standard user (NOT elevated). Use "
                 "run_elevated for a single admin command, or elevate_self to "
                 "relaunch elevated.")


@tool("run_elevated", "Run a single shell command with Administrator rights "
      "(raises the Windows UAC prompt) and return its output. GATED.",
      {"type": "object",
       "properties": {"command": {"type": "string",
                                  "description": "command to run elevated"}},
       "required": ["command"]})
async def run_elevated_tool(command: str) -> str:
    return run_elevated(command)


@tool("elevate_self", "Relaunch eleon itself with Administrator privileges "
      "(opens a new elevated eleon window via UAC). GATED.",
      {"type": "object", "properties": {}})
async def elevate_self() -> str:
    if is_admin():
        return "eleon is already elevated."
    ok = relaunch_as_admin()
    return ("Launched an elevated eleon window — continue there. This window "
            "stays running as standard user." if ok
            else "Elevation was declined or failed.")


# ── Registry ───────────────────────────────────────────────────────
@tool("registry_read", "Read a value from the Windows registry. Read-only.",
      {"type": "object",
       "properties": {"path": {"type": "string",
                               "description": r"e.g. HKCU\Software\MyApp"},
                      "name": {"type": "string",
                               "description": "value name (blank = default)"}},
       "required": ["path"]})
async def registry_read(path: str, name: str = "") -> str:
    try:
        hive, sub = _split(path)
        with winreg.OpenKey(hive, sub) as k:
            val, typ = winreg.QueryValueEx(k, name)
        return f"{path}\\{name or '(default)'} = {val!r} (type {typ})"
    except FileNotFoundError:
        return f"[not found] {path}\\{name}"
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"


@tool("registry_write", "Write a value to the Windows registry (creates the "
      "key if needed). String or DWORD. DESTRUCTIVE — gated.",
      {"type": "object",
       "properties": {"path": {"type": "string"},
                      "name": {"type": "string"},
                      "value": {"type": "string"},
                      "type": {"type": "string", "enum": ["sz", "dword"],
                               "default": "sz"}},
       "required": ["path", "name", "value"]})
async def registry_write(path: str, name: str, value: str,
                         type: str = "sz") -> str:
    try:
        hive, sub = _split(path)
        with winreg.CreateKey(hive, sub) as k:
            if type == "dword":
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))
        return f"Wrote {path}\\{name} = {value} ({type})"
    except PermissionError:
        return ("[access denied] That key needs Administrator rights — retry "
                "via run_elevated or elevate_self first.")
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"


@tool("add_startup", "Make a program launch automatically at login (adds it "
      "to the per-user Run key). Persistent — gated.",
      {"type": "object",
       "properties": {"name": {"type": "string",
                               "description": "startup entry label"},
                      "command": {"type": "string",
                                  "description": "full command / exe path"}},
       "required": ["name", "command"]})
async def add_startup(name: str, command: str) -> str:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, command)
        return f"Added '{name}' to startup → {command}"
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"
