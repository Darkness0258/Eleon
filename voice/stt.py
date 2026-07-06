"""
eleon STT — microphone capture + speech-to-text.

Two pieces:
  record_until_silence()  — capture one spoken utterance from the mic using
                            energy-based voice-activity detection (waits for
                            speech to start, stops after a beat of silence).
  transcribe(wav_bytes)   — send the audio to a Whisper endpoint and return
                            the text.

STT is routed through OpenAI's transcription endpoint (same key as the brain)
with whisper-1 as a fallback, plus Groq Whisper if a valid GROQ key is set.
No audio SDK is needed for transcription — just an HTTP multipart upload.
"""
from __future__ import annotations

import io
import wave

import httpx

from config import (GROQ_API_KEY, GROQ_BASE_URL, OPENAI_API_KEY,
                    OPENAI_BASE_URL, OPENAI_STT_MODEL, VOICE_SAMPLE_RATE)

SR = VOICE_SAMPLE_RATE


# ── Recording (energy VAD) ─────────────────────────────────────────
def record_until_silence(max_seconds: float = 15.0,
                         silence_secs: float = 1.1,
                         start_timeout: float = 8.0) -> bytes | None:
    """
    Record one utterance and return it as WAV bytes (16-bit mono @ SR).

    Waits up to `start_timeout` for speech to begin; once it does, keeps
    recording until `silence_secs` of quiet or `max_seconds` total. Returns
    None if nobody spoke. Requires `sounddevice` (pip install sounddevice).
    """
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"microphone capture needs numpy + sounddevice ({e})")

    block = int(SR * 0.1)  # 100 ms blocks
    frames: list = []

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                        blocksize=block) as stream:
        # Calibrate ambient noise for ~0.3 s to set a speech threshold.
        ambient = []
        for _ in range(3):
            data, _o = stream.read(block)
            ambient.append(_rms(np.frombuffer(data, dtype="int16")))
        thr = max(sum(ambient) / len(ambient) * 3.0 + 90.0, 130.0)

        started = False
        silence_run = 0.0
        elapsed = 0.0
        waited = 0.0
        while True:
            data, _o = stream.read(block)
            arr = np.frombuffer(bytes(data), dtype="int16")
            level = _rms(arr)
            dt = block / SR
            if not started:
                waited += dt
                if level > thr:
                    started = True
                    frames.append(bytes(data))
                elif waited > start_timeout:
                    return None
            else:
                frames.append(bytes(data))
                elapsed += dt
                silence_run = silence_run + dt if level < thr else 0.0
                if silence_run >= silence_secs or elapsed >= max_seconds:
                    break

    if not frames:
        return None
    return _to_wav(b"".join(frames))


def _rms(arr) -> float:
    import numpy as np
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr.astype("float32") ** 2)))


def _to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


# ── Transcription ──────────────────────────────────────────────────
def _tiers() -> list[tuple[str, str, str]]:
    t: list[tuple[str, str, str]] = []
    if OPENAI_API_KEY:
        t.append((OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_STT_MODEL))
        if OPENAI_STT_MODEL != "whisper-1":
            t.append((OPENAI_BASE_URL, OPENAI_API_KEY, "whisper-1"))
    if GROQ_API_KEY:
        t.append((GROQ_BASE_URL, GROQ_API_KEY, "whisper-large-v3-turbo"))
    return t


def transcribe(audio: bytes, filename: str = "speech.wav") -> str:
    """Transcribe audio bytes (wav/mp3/…) to text, trying each STT tier."""
    if not audio:
        return ""
    mime = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    for base, key, model in _tiers():
        try:
            r = httpx.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, mime)},
                data={"model": model, "response_format": "json"},
                timeout=60,
            )
            if r.status_code == 200:
                return (r.json().get("text") or "").strip()
        except Exception:  # noqa: BLE001 — try the next tier
            continue
    return ""
