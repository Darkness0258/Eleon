"""
eleon Elevation — Windows UAC / administrator helpers.

Three capabilities:
  - is_admin()          : are we running with an Administrator token?
  - run_elevated(cmd)   : run ONE command elevated (raises the UAC prompt),
                          capturing its output.
  - relaunch_as_admin() : relaunch the whole eleon process elevated.

Elevation is a real security boundary. The TOOLS that wrap these helpers
(run_elevated, elevate_self) are listed in config.CONFIRM_TOOLS, so the model
must get Boss's confirmation first — and the Windows UAC dialog is a second,
OS-enforced gate that eleon cannot bypass or suppress.
"""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def is_admin() -> bool:
    """True if the current process holds an Administrator token."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_elevated(command: str, timeout: int = 180) -> str:
    """
    Run `command` with an elevated token and return its combined output.

    If eleon is already elevated, the command runs directly. Otherwise it is
    launched through `Start-Process -Verb RunAs`, which triggers the Windows
    UAC consent dialog. Because -Verb RunAs cannot be combined with output
    redirection, the command is wrapped in a temp batch file that redirects
    its own stdout/stderr to a temp file we read back afterwards.
    """
    if is_admin():
        try:
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, encoding="utf-8", errors="ignore",
                               timeout=timeout)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            return out or f"(exit {r.returncode}, no output)"
        except Exception as e:  # noqa: BLE001
            return f"[error] {e}"

    # A fresh private directory per call (randomised name, user-owned). This
    # avoids the TOCTOU window a predictable, world-writable temp name would
    # open — another user can't pre-plant the batch file we're about to run
    # elevated — and prevents two concurrent turns clobbering each other.
    workdir = tempfile.mkdtemp(prefix="eleon_elev_")
    cmd_file = Path(workdir) / "run.cmd"
    out_file = Path(workdir) / "out.txt"
    try:
        # \r\n line endings so cmd.exe parses the batch reliably.
        cmd_file.write_text(
            f'@echo off\r\n{command} > "{out_file}" 2>&1\r\n',
            encoding="utf-8")
        ps = (f"Start-Process -FilePath cmd -ArgumentList '/c','\"{cmd_file}\"' "
              f"-Verb RunAs -Wait")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            return ("[elevation cancelled] The UAC prompt was declined or "
                    f"failed. {err}")
        try:
            out = out_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            out = ""
        return out or "(elevated command ran; produced no output)"
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def relaunch_as_admin() -> bool:
    """
    Relaunch the current eleon process elevated (opens a new elevated window
    via UAC). Returns True if the elevated launch was accepted. The caller
    decides whether to keep the current (non-elevated) process running.
    """
    if is_admin():
        return True
    try:
        params = " ".join(f'"{a}"' for a in sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1)
        return rc > 32  # ShellExecute returns >32 on success
    except Exception:
        return False
