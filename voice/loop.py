"""
eleon voice loop — hands-free wake-word control.

Flow per cycle:
  1. Listen (energy VAD) for one spoken utterance and transcribe it.
  2. Require the wake word ("eleon") unless --always was passed; strip it and
     treat the remainder as the command (if the user only said the wake word,
     eleon says "Yes?" and listens again).
  3. Run the command through the same Agent/Guard as every other front-end.
  4. Speak the reply.

Gated actions are confirmed BY VOICE: eleon reads the action aloud and listens
for a yes/no, so the safety gate still stands in hands-free mode. Say
"stop listening" (or quit/goodbye) to exit; Ctrl+C also works.
"""
from __future__ import annotations

import asyncio

from config import ASSISTANT_NAME, USER_NAME, WAKE_WORD
from core.agent import Agent
from core.safety import Guard
from voice.stt import record_until_silence, transcribe
from voice.tts import speak

_YES = ("yes", "yeah", "yep", "sure", "confirm", "proceed", "do it",
        "go ahead", "okay", "ok", "affirmative")
_STOP = ("stop listening", "stop the assistant", "goodbye", "good bye",
         "quit", "exit", "shut down eleon")


def _print_event(kind: str, data: dict):
    if kind == "tool_call":
        args = ", ".join(f"{k}={str(v)[:30]}" for k, v in data["args"].items())
        print(f"    -> {data['name']}({args})")
    elif kind == "tool_result":
        print(f"       {str(data['result']).splitlines()[0][:90]}")


async def _voice_confirm(tool: str, args: dict, reason: str) -> bool:
    detail = ", ".join(f"{k} {v}" for k, v in (args or {}).items())
    speak(f"Confirm {tool.replace('_', ' ')}? {reason}. Say yes to proceed.")
    print(f"  [confirm] {tool} — {reason} | say yes/no")
    wav = record_until_silence(max_seconds=5, silence_secs=1.0, start_timeout=6)
    ans = transcribe(wav, "confirm.wav").lower() if wav else ""
    ok = any(w in ans for w in _YES)
    print(f"  [confirm heard: {ans!r} -> {'APPROVED' if ok else 'CANCELLED'}]")
    return ok


def voice_chat(always: bool = False) -> None:
    guard = Guard(confirm=_voice_confirm)
    agent = Agent(guard, on_event=_print_event)
    loop = asyncio.new_event_loop()

    mode = "always-on" if always else f"wake word '{WAKE_WORD}'"
    print(f"[voice] {ASSISTANT_NAME} listening ({mode}). "
          f"Say 'stop listening' or press Ctrl+C to exit.")
    speak(f"Hi {USER_NAME}. {ASSISTANT_NAME} is listening.")

    try:
        while True:
            try:
                wav = record_until_silence()
            except RuntimeError as e:
                print(f"[voice] {e}")
                return
            if not wav:
                continue
            heard = transcribe(wav, "speech.wav").strip()
            if not heard:
                continue
            print(f"You (voice): {heard}")
            low = heard.lower()

            if any(s in low for s in _STOP):
                break

            command = heard
            if not always:
                if WAKE_WORD not in low:
                    continue  # not addressed to eleon
                command = heard[low.find(WAKE_WORD) + len(WAKE_WORD):].strip(
                    " ,.:!?-")
                if not command:
                    speak("Yes?")
                    wav2 = record_until_silence()
                    command = (transcribe(wav2, "speech.wav").strip()
                               if wav2 else "")
                    if not command:
                        continue

            print(f"[voice] command: {command}")
            try:
                reply = loop.run_until_complete(agent.run_turn(command))
            except Exception as e:  # noqa: BLE001
                reply = f"Something went wrong: {e}"
            print(f"{ASSISTANT_NAME}: {reply}\n")
            speak(reply)
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()

    print(f"[voice] stopped.")
    speak("Goodbye.")
