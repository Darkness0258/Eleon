"""
eleon — central configuration.

Holds API keys, model selection (with fallback tiers), filesystem paths,
and the safety policy that governs which tool calls require confirmation.

Design notes
------------
eleon's brain uses NATIVE tool-calling (the OpenAI/OpenRouter `tools` API),
not a fixed intent list. The model is handed a toolbox and decides which
tools to call, in what order, reacting to each result. That is what makes
"do anything" possible instead of a fixed menu.

Model tiers (tried in order until one answers):
  1. OpenRouter  — a strong tool-calling model (primary)
  2. Groq        — fast, cheap, good tool-calling (fallback)
  3. Ollama      — local, offline last resort (may not support tools well)
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR  = BASE_DIR / "logs"
DB_PATH  = BASE_DIR / "eleon.db"

load_dotenv(BASE_DIR / ".env")

# ── API keys ───────────────────────────────────────────────────────
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")

# ── Model tiers ────────────────────────────────────────────────────
# Primary: OpenAI gpt-4o-mini — cheap, fast, first-class tool-calling. This
# is the reliable workhorse. Override the model via ELEON_OPENAI_MODEL.
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_MODEL    = os.getenv("ELEON_OPENAI_MODEL", "gpt-4o-mini")

# The OpenRouter key here is free-tier, so we chain several FREE models
# that support native tool-calling (verified live). The agent tries each in
# order until one answers — this doubles as resilience against a single
# model being rate-limited (429). Override the primary via env without
# touching code: set ELEON_OPENROUTER_MODEL.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Lead with models that reliably EMIT tool_calls and real content (verified
# live). Pure "reasoning" models that hide output in a reasoning field and
# skip tool calls are avoided as primaries. Free tier is rate-limited (429)
# often, so the deep fallback list is what keeps eleon responsive.
OPENROUTER_MODEL    = os.getenv("ELEON_OPENROUTER_MODEL",
                                "nvidia/nemotron-3-super-120b-a12b:free")

OPENROUTER_FALLBACKS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
]

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL    = os.getenv("ELEON_GROQ_MODEL", "llama-3.3-70b-versatile")

OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL    = os.getenv("ELEON_OLLAMA_MODEL", "")  # empty = auto-pick


def brain_tiers() -> list[dict]:
    """
    Ordered brain tiers. Each: {label, base_url, api_key, model}. A tier is
    skipped only if it has no usable key. Groq is included when a key is
    present but self-skips at call time if the key is rejected.
    """
    tiers: list[dict] = []
    if OPENAI_API_KEY:
        tiers.append({"label": f"openai:{OPENAI_MODEL}",
                      "base_url": OPENAI_BASE_URL,
                      "api_key": OPENAI_API_KEY, "model": OPENAI_MODEL})
    if OPENROUTER_API_KEY:
        for model in [OPENROUTER_MODEL, *OPENROUTER_FALLBACKS]:
            tiers.append({"label": f"openrouter:{model.split('/')[-1]}",
                          "base_url": OPENROUTER_BASE_URL,
                          "api_key": OPENROUTER_API_KEY, "model": model})
    if GROQ_API_KEY:
        tiers.append({"label": "groq", "base_url": GROQ_BASE_URL,
                      "api_key": GROQ_API_KEY, "model": GROQ_MODEL})
    # Ollama needs no key; include it as the offline last resort.
    tiers.append({"label": "ollama", "base_url": OLLAMA_BASE_URL,
                  "api_key": "ollama", "model": OLLAMA_MODEL or "llama3.1"})
    return tiers

# ── Voice (Phase 4) ────────────────────────────────────────────────
# STT runs through OpenAI's transcription endpoint (same key as the brain),
# with whisper-1 as a fallback and Groq Whisper if a valid GROQ key exists.
OPENAI_STT_MODEL  = os.getenv("ELEON_STT_MODEL", "gpt-4o-mini-transcribe")
TTS_VOICE         = os.getenv("ELEON_TTS_VOICE", "en-US-AriaNeural")
WAKE_WORD         = os.getenv("ELEON_WAKE_WORD", "eleon").lower()
VOICE_SAMPLE_RATE = 16000  # Hz; what Whisper models expect

# ── Agent loop ─────────────────────────────────────────────────────
MAX_STEPS       = 20     # hard cap on tool-call iterations per user turn
REQUEST_TIMEOUT = 60     # seconds per LLM call
TEMPERATURE     = 0.4    # lower = more deterministic tool use

# ── Safety policy ──────────────────────────────────────────────────
# Tools whose NAME is listed here always require explicit confirmation
# before running, regardless of arguments. Shell/registry tools are
# additionally screened by pattern in core/safety.py.
CONFIRM_TOOLS = {
    "delete_path",
    "shutdown",
    "restart",
    "kill_process",
    "registry_write",
    "run_elevated",     # runs a command as Administrator → always gated
    "elevate_self",     # relaunches eleon as Administrator → always gated
    "add_startup",      # persistence at login → always gated
    "empty_recycle_bin",
    "run_powershell",   # can do anything → always gated
    "run_shell",        # can do anything → gated unless clearly read-only
}

# When True, eleon prints a one-line audit entry to the console as well
# as writing to logs/audit.log.
AUDIT_TO_CONSOLE = True

# Identity / persona for the system prompt.
ASSISTANT_NAME = "eleon"
USER_NAME      = "Boss"

SYSTEM_PROMPT = f"""You are {ASSISTANT_NAME}, a high-capability desktop assistant with full control of {USER_NAME}'s Windows laptop and access to the internet.

You operate as an autonomous agent: you are given a toolbox and you accomplish goals by calling tools, observing their results, and calling more tools until the task is complete. Think step by step.

Principles:
- Prefer doing over describing. If a tool can accomplish the request, call it.
- Chain tools: inspect state, act, verify. Use results to decide the next step.
- Be concise in prose. Report what you did in one or two sentences.
- For information you don't know, use web_search / fetch_url rather than guessing.
- Destructive or irreversible actions (delete, shutdown, registry writes, raw shell) are gated and will ask {USER_NAME} to confirm — that is expected; propose them when they're the right move.
- Never fabricate a tool result. Only state what the tools actually returned.
- When the goal is met, stop calling tools and give a short final answer.

You are running on Windows. Paths use backslashes. The user is "{USER_NAME}"."""
