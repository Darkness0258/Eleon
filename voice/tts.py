"""
eleon TTS — text-to-speech with a graceful fallback chain.

speak(text) tries, in order:
  1. edge-tts  — Microsoft's neural voices (needs internet). Synthesised to a
     temp mp3 and played through the Windows MCI API (ctypes, no extra deps).
  2. SAPI      — Windows' built-in System.Speech via win32com. Offline, always
     available, more robotic. This guarantees eleon can always talk back.

The edge-tts synthesis runs inside a dedicated thread with its own asyncio
loop, so speak() is safe to call whether or not the caller is already inside
a running event loop (e.g. from the async safety-confirm callback).
"""
from __future__ import annotations

import ctypes
import os
import tempfile
import threading

from config import TTS_VOICE


def speak(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    if _edge_speak(text):
        return
    _sapi_speak(text)


# ── edge-tts → mp3 → MCI ───────────────────────────────────────────
def _edge_speak(text: str) -> bool:
    try:
        fd, path = tempfile.mkstemp(prefix="eleon_tts_", suffix=".mp3")
        os.close(fd)
    except Exception:
        return False
    try:
        if not _synth_edge(text, path) or os.path.getsize(path) == 0:
            return False
        _play_mp3(path)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def _synth_edge(text: str, path: str) -> bool:
    """Run edge-tts in a fresh thread+loop; returns True on success."""
    result = {"ok": False}

    def work():
        try:
            import asyncio

            import edge_tts
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    edge_tts.Communicate(text, TTS_VOICE).save(path))
                result["ok"] = True
            finally:
                loop.close()
        except Exception:  # noqa: BLE001 — caller falls back to SAPI
            result["ok"] = False

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=30)
    return result["ok"]


def _play_mp3(path: str) -> None:
    """Play an mp3 synchronously via the Windows MCI API (winmm)."""
    mci = ctypes.windll.winmm.mciSendStringW
    alias = "eleon_tts"
    mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0)
    try:
        mci(f"play {alias} wait", None, 0, 0)
    finally:
        mci(f"close {alias}", None, 0, 0)


# ── SAPI fallback ──────────────────────────────────────────────────
def _sapi_speak(text: str) -> None:
    try:
        import win32com.client
        win32com.client.Dispatch("SAPI.SpVoice").Speak(text)
    except Exception:
        pass  # nothing left to try; stay silent rather than crash a turn
