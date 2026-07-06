![eleon — ultimate AI assistant for Windows](assets/eleon-banner.svg)

# eleon

The ultimate AI assistant for Windows — a high-capability desktop agent.
Unlike a fixed-command assistant, eleon runs an **agentic tool-calling loop**:
it's handed a toolbox and a goal, then calls tools, reads the results, and
calls more tools until the goal is done. That's what lets it "do anything"
instead of matching a fixed menu.

- **Full PC control** — launch/close apps, manage files, processes, volume,
  brightness, media keys, windows, power, screenshots, and arbitrary shell /
  PowerShell / Python.
- **Real internet** — web search, page fetching, file downloads, public-IP
  and connectivity checks.
- **Admin / OS-level** — UAC self-elevation, run-a-command-as-Administrator,
  registry read/write, and login-startup persistence.
- **Persistent memory** — remembers facts about you and past conversations in
  a local SQLite DB, and starts every session already knowing them.
- **Admin-capable, safe by default** — destructive/irreversible actions
  (delete, shutdown, registry writes, elevation, startup, raw shell) pause for
  a `[y/N]` confirmation, and the Windows UAC dialog is a second OS-enforced
  gate. Everything is written to an append-only audit log.
- **Reliable brain with fallback** — OpenAI `gpt-4o-mini` primary, a chain of
  free OpenRouter tool-calling models as fallback, local Ollama last.

## Status: Phases 1–4 all working

46 tools, the agent loop, the safety gate, the audit log, UAC elevation,
persistent memory, a PyQt6 desktop GUI, **and hands-free voice** (wake word →
Whisper STT → agent → neural TTS) are all live and tested.

## Quick start

```bat
install.bat                       :: creates .venv, installs deps
.venv\Scripts\python run.py         :: interactive chat (text CLI)
.venv\Scripts\python run.py --gui   :: PyQt6 desktop window
.venv\Scripts\python run.py --voice :: hands-free voice (wake word 'eleon')
```

Or without the venv (core deps only):

```bash
python -m pip install httpx python-dotenv psutil
python run.py
```

Commands inside the chat: `reset` clears context, `quit` exits.

### Verify it works

```bash
python run.py --selftest         # loop: model -> system_info tool -> answer
python tests/test_safety.py      # 20 safety-gate classifications (offline)
python tests/test_agent_live.py  # chaining + internet + gate (hits network)
python tests/test_phase2_live.py # memory persistence + internet + admin gate
python tests/test_gui_headless.py # GUI worker bridge + confirm dialog (offscreen)
python tests/test_voice.py        # STT round-trip + TTS synth + mic open
```

## Configuration (`.env`)

| Key | Purpose |
|-----|---------|
| `OPENAI_API_KEY` | Primary brain (`gpt-4o-mini`). |
| `OPENROUTER_API_KEY` | Free fallback models. |
| `GROQ_API_KEY` | Optional fast fallback (skipped if invalid). |

Override models without editing code: `ELEON_OPENAI_MODEL`,
`ELEON_OPENROUTER_MODEL`, `ELEON_OLLAMA_MODEL`.

## Architecture

```
run.py            entry: text CLI (default), --gui, --selftest
config.py         models, paths, safety policy, system prompt
ui/
  gui.py          PyQt6 window (🎤 mic input + 🔊 spoken replies); agent runs
                  on a worker thread, voice capture/TTS on their own threads
voice/
  stt.py          mic capture (VAD) + Whisper transcription (OpenAI/Groq)
  tts.py          edge-tts neural voice → MCI playback, SAPI fallback
  loop.py         wake-word listen → agent → speak; voice-confirmed gating
core/
  agent.py        the loop: think -> call tool -> observe -> repeat (MAX_STEPS)
  brain.py        LLM client (OpenAI dialect) + tiered fallback + 429 backoff
  tools.py        tool registry: JSON schemas in, dispatch by name out
  safety.py       risk classifier + confirm gate + append-only audit log
  memory.py       SQLite facts + conversation history, injected each session
  elevate.py      UAC helpers: is_admin, run_elevated, relaunch_as_admin
tools/
  os_tools.py     apps, files, processes, power, volume, screenshot
  shell_tools.py  run_shell / run_powershell / run_python / install_package
  web_tools.py    web_search, fetch_url, download_file
  admin_tools.py  check_admin, run_elevated, elevate_self, registry_*, add_startup
  win_tools.py    network, clipboard, media keys, brightness, windows, recycle bin
  memory_tools.py remember, recall, list_memories, forget
eleon.db          local memory (facts + conversation history)
logs/audit.log    every tool decision + result, timestamped
```

### How a turn flows

1. Your message is added to the conversation; the brain is called with the
   full tool schema.
2. If it returns tool calls, each one passes through the safety `Guard`
   (confirm gate + audit), runs, and its result is fed back.
3. The loop repeats until the model gives a final answer or hits `MAX_STEPS`.

### Safety model

- Tools in `config.CONFIRM_TOOLS` always confirm (delete, shutdown, restart,
  kill_process, registry_write, run_elevated, elevate_self, add_startup,
  empty_recycle_bin, raw shell/PowerShell).
- Shell/PowerShell arguments are additionally pattern-screened, so an
  innocently-named call can't smuggle a `format C:` through. Clearly
  read-only commands (`dir`, `ipconfig`, `Get-*`) run without a prompt.
- Elevation adds a second, OS-enforced gate: `run_elevated` / `elevate_self`
  raise the Windows UAC dialog, which eleon cannot bypass or suppress.
- With no confirm handler attached, gated actions are auto-denied.

## Roadmap

- ~~**Phase 2** — more OS tools, admin self-elevation (UAC), persistent memory.~~ ✅ done
- ~~**Phase 3** — PyQt6 desktop GUI (chat + live status + confirm dialogs).~~ ✅ done
- ~~**Phase 4** — voice: wake word, Whisper STT, TTS.~~ ✅ done

All four phases are complete, and the GUI now has a 🎤 mic button and a 🔊
"speak replies" toggle. Possible next steps: richer proactive behaviours, or
packaging as a single `.exe`.

## Notes

- Python 3.14 is very new; `pyautogui`/`PyQt6` wheels may lag. The core CLI
  needs only `httpx`, `python-dotenv`, `psutil`. The `screenshot` tool
  degrades gracefully if `pyautogui` isn't installed.
- Free OpenRouter models rate-limit (HTTP 429) in bursts; that's why OpenAI
  is the primary and the brain retries across tiers with backoff.
