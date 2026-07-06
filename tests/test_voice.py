"""
Voice-stack test (no microphone speech, no audible playback).
Run:  python tests/test_voice.py

  1. STT round-trip: synthesize a known phrase with edge-tts, transcribe it
     back through the real OpenAI endpoint, and check the words survive.
  2. TTS: edge-tts synthesizes a non-empty mp3 and Windows MCI can open it.
  3. Recorder: opens the mic and returns cleanly on the no-speech timeout.

Hits the network (edge-tts + OpenAI). Exit code is non-zero on hard failure
of the STT round-trip (the load-bearing path); mic/playback are reported but
tolerated since they depend on local hardware.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    import edge_tts
    from config import TTS_VOICE
    from voice.stt import transcribe, record_until_silence
    from voice.tts import _synth_edge, _play_mp3

    phrase = "Open Chrome and check the weather in Karachi."

    # Synthesize once, reuse the mp3 for both STT and TTS checks.
    mp3 = str(Path(__file__).resolve().parent / "_voice_probe.mp3")
    asyncio.run(edge_tts.Communicate(phrase, TTS_VOICE).save(mp3))
    size = Path(mp3).stat().st_size
    print(f"[voice] 1. edge-tts synth: {size} bytes")
    assert size > 0, "edge-tts produced no audio"

    # 1. STT round-trip
    audio = Path(mp3).read_bytes()
    text = transcribe(audio, "probe.mp3")
    print(f"[voice] 2. STT transcript: {text!r}")
    lowered = text.lower()
    hits = sum(w in lowered for w in ("chrome", "weather", "karachi"))
    stt_ok = hits >= 2
    print(f"[voice]    keyword hits: {hits}/3 -> {'OK' if stt_ok else 'FAIL'}")

    # 2. TTS synth + MCI open (no audible play)
    import ctypes
    mci = ctypes.windll.winmm.mciSendStringW
    rc_open = mci(f'open "{mp3}" type mpegvideo alias probe', None, 0, 0)
    mci("close probe", None, 0, 0)
    print(f"[voice] 3. MCI open rc={rc_open} (0 == playable)")

    Path(mp3).unlink(missing_ok=True)

    # 3. Recorder: should return cleanly (None on silence) without raising.
    mic_ok = True
    try:
        clip = record_until_silence(start_timeout=0.8, silence_secs=0.6,
                                    max_seconds=3)
        print(f"[voice] 4. recorder returned: "
              f"{'audio' if clip else 'None (no speech)'}")
    except Exception as e:  # noqa: BLE001
        mic_ok = False
        print(f"[voice] 4. recorder unavailable: {e}")

    ok = stt_ok
    print(f"\n[voice] {'PASS' if ok else 'FAIL'}: STT round-trip "
          f"{'works' if ok else 'failed'} | mp3 open rc={rc_open} | "
          f"mic {'ok' if mic_ok else 'n/a'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
