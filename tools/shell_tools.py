"""
eleon shell tools — arbitrary execution surface.

These are what let eleon truly "do anything": run any command, PowerShell
script, or Python snippet, and install software. Precisely because they are
unbounded, run_shell / run_powershell are listed in config.CONFIRM_TOOLS and
screened for destructive patterns by core/safety.py. A read-only command
(dir, ipconfig, get-*) is allowed to run without a prompt so the agent stays
responsive.

Output is captured and truncated so a chatty command can't flood the model
context.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from core.tools import tool

_MAX_OUT = 4000

# A package name/spec should never contain shell metacharacters. Rejecting
# them closes command-injection via the (ungated) install_package tool while
# still allowing legit specifiers like requests==2.3, pkg[extra], @scope/pkg.
_BAD_PKG = re.compile(r"""[\s&|;<>^`"'()${}%\n\r]""")


def _run(cmd: list[str] | str, shell: bool, timeout: int) -> str:
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="ignore")
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
        out = out.strip() or "(no output)"
        return out[:_MAX_OUT]
    except subprocess.TimeoutExpired:
        return "[error] command timed out"
    except Exception as e:
        return f"[error] {e}"


@tool("run_shell", "Run a Windows shell (cmd) command and return its output. "
      "Gated for anything not clearly read-only.",
      {"type": "object",
       "properties": {"command": {"type": "string"},
                      "timeout": {"type": "integer", "default": 30}},
       "required": ["command"]})
async def run_shell(command: str, timeout: int = 30) -> str:
    return _run(command, shell=True, timeout=int(timeout))


@tool("run_powershell", "Run a PowerShell script and return its output. Gated.",
      {"type": "object",
       "properties": {"script": {"type": "string"},
                      "timeout": {"type": "integer", "default": 30}},
       "required": ["script"]})
async def run_powershell(script: str, timeout: int = 30) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 script], shell=False, timeout=int(timeout))


@tool("run_python", "Execute a short Python snippet in a subprocess and return "
      "stdout/stderr.",
      {"type": "object",
       "properties": {"code": {"type": "string"},
                      "timeout": {"type": "integer", "default": 20}},
       "required": ["code"]})
async def run_python(code: str, timeout: int = 20) -> str:
    # Unique temp file per call so concurrent turns can't overwrite each
    # other's snippet; run it with the current interpreter.
    fd, tmp = tempfile.mkstemp(prefix="eleon_exec_", suffix=".py")
    try:
        os.close(fd)
        Path(tmp).write_text(code, encoding="utf-8")
        return _run([sys.executable, tmp], shell=False, timeout=int(timeout))
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


@tool("install_package", "Install software. manager is one of: winget, pip, "
      "npm. Runs the appropriate install command.",
      {"type": "object",
       "properties": {"manager": {"type": "string",
                                  "enum": ["winget", "pip", "npm"]},
                      "package": {"type": "string"}},
       "required": ["manager", "package"]})
async def install_package(manager: str, package: str) -> str:
    manager = manager.lower().strip()
    package = (package or "").strip()
    if not package or _BAD_PKG.search(package):
        return "[error] package name contains illegal characters"
    if manager == "winget":
        # winget.exe is a real executable → no shell needed.
        cmd = ["winget", "install", "--id", package, "-e",
               "--accept-source-agreements", "--accept-package-agreements"]
        return _run(cmd, shell=False, timeout=180)
    if manager == "pip":
        # Run pip via the current interpreter → no shell, no PATH ambiguity.
        return _run([sys.executable, "-m", "pip", "install", package],
                    shell=False, timeout=180)
    if manager == "npm":
        # npm is a .cmd shim, so it needs a shell to resolve; the package name
        # is already validated above, so interpolation is safe here.
        return _run(f"npm install -g {package}", shell=True, timeout=180)
    return f"[error] unknown manager: {manager}"
