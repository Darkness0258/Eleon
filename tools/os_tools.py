"""
eleon OS tools — Windows control surface.

Safe-by-default building blocks the agent composes: launch apps, manage
files, inspect the system, control power/volume. Destructive members
(delete_path, shutdown, restart, kill_process) are registered here but gated
by core/safety.py before they ever run.

Every function returns a short human-readable string (the observation).
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

from core.tools import tool

try:
    import psutil
    _PS = True
except Exception:
    _PS = False

HOME = Path.home()

# ── App launcher (registry + PATH + shell fallback) ────────────────
_APPS = {
    "chrome":     [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
    "firefox":    [r"C:\Program Files\Mozilla Firefox\firefox.exe"],
    "edge":       [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    "notepad":    [r"C:\Windows\System32\notepad.exe"],
    "calc":       [r"C:\Windows\System32\calc.exe"],
    "calculator": [r"C:\Windows\System32\calc.exe"],
    "explorer":   [r"C:\Windows\explorer.exe"],
    "cmd":        [r"C:\Windows\System32\cmd.exe"],
    "powershell": [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"],
    "taskmgr":    [r"C:\Windows\System32\Taskmgr.exe"],
    "paint":      [r"C:\Windows\System32\mspaint.exe"],
    "code":       [r"C:\Users\mhamz\AppData\Local\Programs\Microsoft VS Code\Code.exe",
                   r"C:\Program Files\Microsoft VS Code\Code.exe"],
    "vscode":     [r"C:\Users\mhamz\AppData\Local\Programs\Microsoft VS Code\Code.exe"],
    "spotify":    [r"C:\Users\mhamz\AppData\Roaming\Spotify\Spotify.exe"],
}


def _resolve(paths: list[str]) -> str | None:
    for p in paths:
        if "*" in p:
            hits = sorted(glob.glob(p), reverse=True)
            if hits:
                return hits[0]
        elif os.path.exists(p):
            return p
    return None


@tool("open_app", "Launch a Windows application by name (e.g. chrome, notepad, "
      "vscode, spotify, calc). Falls back to the shell if not in the known list.",
      {"type": "object",
       "properties": {"app": {"type": "string", "description": "app name"}},
       "required": ["app"]})
async def open_app(app: str) -> str:
    name = app.lower().strip()
    if name in _APPS:
        path = _resolve(_APPS[name])
        if path:
            subprocess.Popen([path])
            return f"Opened {app}"
    # PATH lookup (no shell → the app name can't smuggle shell metacharacters)
    try:
        r = subprocess.run(["where", name], shell=False, capture_output=True,
                           text=True, timeout=4)
        if r.returncode == 0:
            exe = r.stdout.strip().splitlines()[0]
            subprocess.Popen([exe])
            return f"Opened {app}"
    except Exception:
        pass
    # Shell-execute fallback for UWP / protocols / registered apps. os.startfile
    # uses the Windows shell API directly (not cmd), so there is no command
    # string for an injected name to break out of.
    try:
        os.startfile(name)
        return f"Opened {app}"
    except Exception:
        return f"Could not open {app} — not found."


@tool("close_app", "Close/terminate a running application by (partial) process "
      "name.",
      {"type": "object",
       "properties": {"app": {"type": "string"}},
       "required": ["app"]})
async def close_app(app: str) -> str:
    if not _PS:
        return "psutil unavailable"
    killed = set()
    for p in psutil.process_iter(["name"]):
        try:
            if app.lower() in (p.info["name"] or "").lower():
                p.kill()
                killed.add(p.info["name"])
        except Exception:
            continue
    return f"Closed {', '.join(killed)}" if killed else f"Not running: {app}"


@tool("open_url", "Open a URL in the default web browser.",
      {"type": "object",
       "properties": {"url": {"type": "string"}},
       "required": ["url"]})
async def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


@tool("open_folder", "Open a folder in File Explorer.",
      {"type": "object",
       "properties": {"path": {"type": "string"}},
       "required": ["path"]})
async def open_folder(path: str) -> str:
    # No shell → a path containing quotes/&/| can't chain a second command.
    subprocess.Popen(["explorer", path])
    return f"Opened {path}"


# ── Files ──────────────────────────────────────────────────────────
@tool("read_file", "Read a text file and return up to 4000 characters.",
      {"type": "object",
       "properties": {"path": {"type": "string"}},
       "required": ["path"]})
async def read_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")[:4000]
    except Exception as e:
        return f"[error] {e}"


@tool("write_file", "Create or overwrite a text file with the given content.",
      {"type": "object",
       "properties": {"path": {"type": "string"},
                      "content": {"type": "string"}},
       "required": ["path", "content"]})
async def write_file(path: str, content: str = "") -> str:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars → {path}"
    except Exception as e:
        return f"[error] {e}"


@tool("list_dir", "List the contents of a directory.",
      {"type": "object",
       "properties": {"path": {"type": "string", "description": "default: home"}}})
async def list_dir(path: str = "") -> str:
    target = path or str(HOME)
    try:
        entries = sorted(os.listdir(target))
        return "\n".join(entries) if entries else "(empty)"
    except Exception as e:
        return f"[error] {e}"


@tool("delete_path", "Delete a file or folder (recursively). DESTRUCTIVE — "
      "gated by confirmation.",
      {"type": "object",
       "properties": {"path": {"type": "string"}},
       "required": ["path"]})
async def delete_path(path: str) -> str:
    try:
        if os.path.isfile(path):
            os.remove(path)
            return f"Deleted file: {path}"
        if os.path.isdir(path):
            shutil.rmtree(path)
            return f"Deleted folder: {path}"
        return f"Nothing at: {path}"
    except Exception as e:
        return f"[error] {e}"


@tool("move_path", "Move or rename a file/folder.",
      {"type": "object",
       "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
       "required": ["src", "dst"]})
async def move_path(src: str, dst: str) -> str:
    try:
        shutil.move(src, dst)
        return f"Moved {src} → {dst}"
    except Exception as e:
        return f"[error] {e}"


@tool("search_files", "Search for files by name pattern under a folder "
      "(default: home). Returns up to 20 matches.",
      {"type": "object",
       "properties": {"pattern": {"type": "string"},
                      "folder": {"type": "string"}},
       "required": ["pattern"]})
async def search_files(pattern: str, folder: str = "") -> str:
    base = folder or str(HOME)
    hits = glob.glob(os.path.join(base, "**", f"*{pattern}*"), recursive=True)
    return "\n".join(hits[:20]) if hits else "No matches."


# ── System / power / volume ────────────────────────────────────────
@tool("system_info", "Report CPU, RAM, disk, and battery status.",
      {"type": "object", "properties": {}})
async def system_info() -> str:
    if not _PS:
        return "psutil unavailable"
    cpu = psutil.cpu_percent(interval=0.4)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("C:/")
    bat = psutil.sensors_battery()
    bat_s = (f"{bat.percent:.0f}% "
             f"({'charging' if bat.power_plugged else 'on battery'})"
             if bat else "N/A")
    return (f"CPU {cpu}% | RAM {ram.percent}% "
            f"({ram.used // 1024**3}/{ram.total // 1024**3} GB) | "
            f"Disk C: {disk.percent}% | Battery {bat_s}")


@tool("list_processes", "List up to 40 running process names.",
      {"type": "object", "properties": {}})
async def list_processes() -> str:
    if not _PS:
        return "psutil unavailable"
    names = sorted({p.info["name"] for p in psutil.process_iter(["name"])
                    if p.info["name"]})
    return "\n".join(names[:40])


@tool("kill_process", "Kill a process by (partial) name. DESTRUCTIVE — gated.",
      {"type": "object",
       "properties": {"name": {"type": "string"}},
       "required": ["name"]})
async def kill_process(name: str) -> str:
    return await close_app(name)


@tool("set_volume", "Set the system master volume (0-100).",
      {"type": "object",
       "properties": {"level": {"type": "integer", "minimum": 0,
                                "maximum": 100}},
       "required": ["level"]})
async def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    script = (f"$o=New-Object -ComObject WScript.Shell;"
              f"1..50|%{{$o.SendKeys([char]174)}};"
              f"1..{int(level/2)}|%{{$o.SendKeys([char]175)}}")
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   capture_output=True, timeout=15)
    return f"Volume set to ~{level}%"


@tool("lock_screen", "Lock the workstation.",
      {"type": "object", "properties": {}})
async def lock_screen() -> str:
    subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
    return "Locked."


@tool("shutdown", "Shut down the PC after an optional delay (seconds). "
      "DESTRUCTIVE — gated.",
      {"type": "object",
       "properties": {"delay": {"type": "integer", "default": 0}}})
async def shutdown(delay: int = 0) -> str:
    subprocess.run(f"shutdown /s /t {int(delay)}", shell=True)
    return f"Shutting down in {delay}s."


@tool("restart", "Restart the PC after an optional delay (seconds). "
      "DESTRUCTIVE — gated.",
      {"type": "object",
       "properties": {"delay": {"type": "integer", "default": 0}}})
async def restart(delay: int = 0) -> str:
    subprocess.run(f"shutdown /r /t {int(delay)}", shell=True)
    return f"Restarting in {delay}s."


@tool("screenshot", "Capture the screen to a PNG in Pictures and return its "
      "path.",
      {"type": "object",
       "properties": {"name": {"type": "string"}}})
async def screenshot(name: str = "") -> str:
    try:
        import pyautogui
    except Exception:
        return "pyautogui unavailable — install it for screenshots."
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(HOME / "Pictures" / (name or f"eleon_{ts}.png"))
    pyautogui.screenshot(path)
    return f"Saved screenshot → {path}"
