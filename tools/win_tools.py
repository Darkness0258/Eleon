"""
eleon Windows extras — network, clipboard, media, display, windows.

Everyday desktop conveniences that round out eleon's control surface. All are
low-risk except empty_recycle_bin (destructive → gated). Each returns a short
observation string.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from pathlib import Path

from core.tools import tool

try:
    import httpx
    _HTTPX = True
except Exception:
    _HTTPX = False


def _ps(cmd: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="ignore", timeout=timeout)


# ── Network ────────────────────────────────────────────────────────
@tool("network_info", "Report this machine's hostname and primary local IP "
      "address.", {"type": "object", "properties": {}})
async def network_info() -> str:
    import socket
    host = socket.gethostname()
    ip = "unknown"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(host)
        except Exception:
            pass
    finally:
        s.close()  # always released, even if connect() raised
    return f"Hostname: {host} | Local IP: {ip}"


@tool("wifi_info", "Show the current Wi-Fi connection (SSID, signal, speed).",
      {"type": "object", "properties": {}})
async def wifi_info() -> str:
    r = _ps("netsh wlan show interfaces")
    keep = ("SSID", "Signal", "State", "Receive rate", "Transmit rate",
            "Radio type", "Band", "Channel")
    lines = [ln.strip() for ln in (r.stdout or "").splitlines()
             if any(k in ln for k in keep)]
    return "\n".join(lines) if lines else "No Wi-Fi interface / not connected."


@tool("public_ip", "Look up this machine's public IP address over the "
      "internet.", {"type": "object", "properties": {}})
async def public_ip() -> str:
    if not _HTTPX:
        return "httpx unavailable"
    try:
        r = httpx.get("https://api.ipify.org", params={"format": "json"},
                      timeout=10)
        return f"Public IP: {r.json().get('ip', '?')}"
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"


@tool("ping_host", "Ping a host 4 times and report reachability/latency.",
      {"type": "object",
       "properties": {"host": {"type": "string"}},
       "required": ["host"]})
async def ping_host(host: str) -> str:
    try:
        r = subprocess.run(f"ping -n 4 {host}", shell=True, capture_output=True,
                           text=True, encoding="utf-8", errors="ignore",
                           timeout=20)
        tail = [ln for ln in r.stdout.splitlines()
                if "Average" in ln or "Lost" in ln or "unreachable" in ln.lower()]
        return "\n".join(tail) or r.stdout[-300:]
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"


# ── Clipboard ──────────────────────────────────────────────────────
@tool("clipboard_get", "Read the current text contents of the clipboard.",
      {"type": "object", "properties": {}})
async def clipboard_get() -> str:
    # Force UTF-8 output so non-ASCII clipboard text survives the pipe back.
    r = _ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw")
    out = (r.stdout or "").strip()
    return out or "(clipboard empty)"


@tool("clipboard_set", "Replace the clipboard contents with the given text.",
      {"type": "object",
       "properties": {"text": {"type": "string"}},
       "required": ["text"]})
async def clipboard_set(text: str) -> str:
    # Round-trip via a UTF-8 temp file (a stdin pipe mangles non-ASCII under
    # the console code page). The randomised filename means two concurrent
    # turns can't clobber each other, and it's removed afterwards. The temp
    # path is machine-owned with no quote chars, so the single-quoted PS
    # argument is safe.
    fd, path = tempfile.mkstemp(prefix="eleon_clip_", suffix=".txt")
    try:
        os.close(fd)
        Path(path).write_text(text, encoding="utf-8")
        _ps(f"Get-Content -Raw -Encoding UTF8 '{path}' | Set-Clipboard")
        return f"Copied {len(text)} chars to clipboard."
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ── Media / volume keys ────────────────────────────────────────────
_MEDIA_VK = {
    "playpause": 0xB3, "play": 0xB3, "pause": 0xB3,
    "next": 0xB0, "prev": 0xB1, "previous": 0xB1, "stop": 0xB2,
    "mute": 0xAD, "volup": 0xAF, "voldown": 0xAE,
}


@tool("media_control", "Send a media key: playpause, next, prev, stop, mute, "
      "volup, voldown.",
      {"type": "object",
       "properties": {"action": {"type": "string",
                                 "enum": list(_MEDIA_VK)}},
       "required": ["action"]})
async def media_control(action: str) -> str:
    vk = _MEDIA_VK.get(action.lower())
    if vk is None:
        return f"Unknown action: {action}"
    # Media keys are extended keys; supply the real scan code and the
    # EXTENDEDKEY flag so strict input drivers accept the synthetic press.
    user32 = ctypes.windll.user32
    scan = user32.MapVirtualKeyW(vk, 0)
    ext, keyup = 0x01, 0x02  # KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP
    user32.keybd_event(vk, scan, ext, 0)
    user32.keybd_event(vk, scan, ext | keyup, 0)
    return f"Sent media key: {action}"


# ── Display ────────────────────────────────────────────────────────
@tool("set_brightness", "Set the laptop display brightness (0-100). Requires "
      "a WMI-controllable panel (most laptops).",
      {"type": "object",
       "properties": {"level": {"type": "integer", "minimum": 0,
                                "maximum": 100}},
       "required": ["level"]})
async def set_brightness(level: int) -> str:
    level = max(0, min(100, int(level)))
    r = _ps("(Get-WmiObject -Namespace root/WMI -Class "
            f"WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})")
    if r.returncode == 0:
        return f"Brightness set to {level}%"
    return f"[error] brightness not settable on this display ({r.stderr.strip()})"


# ── Window management ──────────────────────────────────────────────
@tool("show_desktop", "Minimise every window to show the desktop.",
      {"type": "object", "properties": {}})
async def show_desktop() -> str:
    _ps("(New-Object -ComObject Shell.Application).MinimizeAll()")
    return "Minimised all windows."


@tool("restore_windows", "Undo 'show desktop' — restore minimised windows.",
      {"type": "object", "properties": {}})
async def restore_windows() -> str:
    _ps("(New-Object -ComObject Shell.Application).UndoMinimizeALL()")
    return "Restored windows."


# ── Recycle bin ────────────────────────────────────────────────────
@tool("empty_recycle_bin", "Permanently empty the Recycle Bin. DESTRUCTIVE — "
      "gated.", {"type": "object", "properties": {}})
async def empty_recycle_bin() -> str:
    # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND = 0x7
    try:
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x07)
        return "Recycle Bin emptied." if res == 0 else \
            "Recycle Bin already empty or unavailable."
    except Exception as e:  # noqa: BLE001
        return f"[error] {e}"
