"""
eleon GUI — a modern PyQt6 desktop front-end with an always-on, voice-reactive
interface.

Threads (nothing blocking ever runs on the GUI thread):
  AgentWorker  — the Agent + its own asyncio loop; talks to the UI via signals.
  VoiceEngine  — holds the mic open continuously: streams amplitude to the
                 VoiceOrb visualiser AND runs wake-word VAD capture, emitting a
                 transcript when you finish speaking.
  SpeakWorker  — one-shot TTS playback.

Voice flow (feedback-safe): the engine listens continuously; when it hears the
wake word it hands the command to the agent and is PAUSED for the whole
turn + spoken reply, so eleon never hears itself. The orb reflects state:
listening (reacts to your voice), thinking, or speaking.

The confirm bridge is unchanged: guard.confirm is async and blocks the agent
until Boss answers a modal, via a threading.Event.
"""
from __future__ import annotations

import asyncio
import math
import threading

from PyQt6.QtCore import Qt, QThread, QTimer, QPointF, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QPainter, QPen, QRadialGradient)
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from config import ASSISTANT_NAME, USER_NAME, WAKE_WORD
from core.agent import Agent
from core.safety import Guard


# ── Worker: the agent + its own asyncio loop, on a background thread ──
class AgentWorker(QThread):
    event_signal    = pyqtSignal(str, object)
    reply_signal    = pyqtSignal(str)
    error_signal    = pyqtSignal(str)
    busy_signal     = pyqtSignal(bool)
    status_signal   = pyqtSignal(str)
    confirm_request = pyqtSignal(str, object, str)

    def __init__(self):
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self.agent: Agent | None = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue()
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self):
        guard = Guard(confirm=self._confirm)
        self.agent = Agent(guard, on_event=self._on_event)
        self.status_signal.emit(self.agent.brain.active_label)
        while True:
            cmd, payload = await self._queue.get()
            if cmd == "quit":
                return
            if cmd == "reset":
                self.agent.reset()
                self.event_signal.emit("system", {"text": "context cleared"})
                continue
            if cmd == "turn":
                self.busy_signal.emit(True)
                try:
                    reply = await self.agent.run_turn(payload)
                    self.reply_signal.emit(reply)
                    self.status_signal.emit(self.agent.brain.active_label)
                except Exception as e:  # noqa: BLE001
                    self.error_signal.emit(str(e))
                finally:
                    self.busy_signal.emit(False)

    def _on_event(self, kind: str, data: dict):
        self.event_signal.emit(kind, data)

    async def _confirm(self, tool: str, args: dict, reason: str) -> bool:
        self._confirm_result = False
        self._confirm_event.clear()
        self.confirm_request.emit(tool, args, reason)
        await asyncio.to_thread(self._confirm_event.wait)
        return self._confirm_result

    def resolve_confirm(self, ok: bool):
        self._confirm_result = ok
        self._confirm_event.set()

    def submit(self, text: str):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ("turn", text))

    def reset(self):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ("reset", None))

    def stop(self):
        if self._loop and self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ("quit", None))


# ── Voice engine: continuous mic → level meter + wake-word VAD ───────
class VoiceEngine(QThread):
    level  = pyqtSignal(float)   # 0..1 amplitude, ~20 Hz, for the visualiser
    heard  = pyqtSignal(str)     # a finished utterance, transcribed
    stt    = pyqtSignal()        # emitted when an utterance ends (STT starting)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._run = True
        self._paused = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop_engine(self):
        self._run = False

    def run(self):
        try:
            import numpy as np
            import sounddevice as sd
            from voice.stt import SR, _to_wav, transcribe
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return

        def rms(a) -> float:
            return float(np.sqrt(np.mean(a.astype("float32") ** 2))) if a.size else 0.0

        block = int(SR * 0.05)  # 50 ms → smooth meter
        try:
            with sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                                blocksize=block) as stream:
                amb = []
                for _ in range(6):
                    d, _o = stream.read(block)
                    amb.append(rms(np.frombuffer(bytes(d), dtype="int16")))
                thr = max(sum(amb) / len(amb) * 3.0 + 90.0, 130.0)

                capturing = False
                frames: list = []
                silence = 0.0
                dur = 0.0
                dt = block / SR
                while self._run:
                    d, _o = stream.read(block)
                    arr = np.frombuffer(bytes(d), dtype="int16")
                    self.level.emit(min(1.0, rms(arr) / 3500.0))
                    if self._paused:
                        capturing, frames, silence = False, [], 0.0
                        continue
                    loud = rms(arr) > thr
                    if not capturing:
                        if loud:
                            capturing, frames, silence, dur = True, [bytes(d)], 0.0, 0.0
                    else:
                        frames.append(bytes(d))
                        dur += dt
                        silence = 0.0 if loud else silence + dt
                        if silence >= 1.0 or dur >= 15.0:
                            wav = _to_wav(b"".join(frames))
                            capturing, frames = False, []
                            self.stt.emit()
                            text = transcribe(wav, "speech.wav")
                            if text:
                                self.heard.emit(text)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class SpeakWorker(QThread):
    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def run(self):
        try:
            from voice.tts import speak
            speak(self._text)
        except Exception:  # noqa: BLE001
            pass


# ── Palette (modern deep-navy + teal→violet accents) ────────────────
BG      = "#0a0e15"
PANEL   = "#111823"
ELEV    = "#18202d"
BORDER  = "#232d3d"
ACCENT  = "#4be3d1"   # teal
ACCENT2 = "#7c5cff"   # violet
USERC   = "#2f6bff"   # blue
TXT     = "#e8eef6"
DIM     = "#8a94a6"
DANGER  = "#ff9f57"


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:60]}" for k, v in (args or {}).items())


# ── Voice-reactive orb visualiser ───────────────────────────────────
class VoiceOrb(QWidget):
    """A glowing orb ringed by radial bars that react to the mic level."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self._level = 0.0     # smoothed, what we draw
        self._target = 0.0    # latest mic level
        self._phase = 0.0
        self._mode = "idle"   # idle | listening | processing | speaking
        self._bars = 56
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps

    def set_level(self, v: float):
        self._target = max(0.0, min(1.0, v))

    def set_mode(self, mode: str):
        self._mode = mode

    def _tick(self):
        m = self._mode
        if m in ("processing", "speaking"):
            tgt = 0.45 + 0.35 * abs(math.sin(self._phase * 0.9))
            self._level += (tgt - self._level) * 0.2
        elif m == "idle":
            tgt = 0.10 + 0.06 * abs(math.sin(self._phase * 0.5))
            self._level += (tgt - self._level) * 0.15
        else:  # listening — follow the mic, decay when quiet
            self._level += (self._target - self._level) * 0.35
            self._target *= 0.82
        self._phase += 0.09
        self.update()

    def _colors(self):
        if self._mode == "processing":
            return QColor(124, 92, 255), QColor(60, 80, 180)
        if self._mode == "speaking":
            return QColor(75, 227, 209), QColor(60, 180, 220)
        if self._mode == "idle":
            return QColor(96, 106, 126), QColor(50, 60, 80)
        return QColor(75, 227, 209), QColor(80, 130, 255)  # listening

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        base = min(w, h) * 0.17
        lvl = self._level
        c1, c2 = self._colors()

        # Soft outer glow
        glow_r = base * (1.7 + lvl * 1.2)
        gg = QRadialGradient(cx, cy, glow_r)
        gc = QColor(c1)
        gc.setAlpha(60)
        gg.setColorAt(0.0, gc)
        edge = QColor(c1)
        edge.setAlpha(0)
        gg.setColorAt(1.0, edge)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gg)
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # Radial bars
        R = base
        for i in range(self._bars):
            ang = (i / self._bars) * 2 * math.pi
            var = 0.5 + 0.5 * math.sin(self._phase * 1.6 + i * 0.55)
            amp = base * (0.30 + lvl * 1.7 * var)
            x1, y1 = cx + math.cos(ang) * R, cy + math.sin(ang) * R
            x2, y2 = cx + math.cos(ang) * (R + amp), cy + math.sin(ang) * (R + amp)
            t = i / self._bars
            col = QColor(
                int(c1.red() * (1 - t) + c2.red() * t),
                int(c1.green() * (1 - t) + c2.green() * t),
                int(c1.blue() * (1 - t) + c2.blue() * t))
            pen = QPen(col, 3.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Central orb
        orb_r = base * (0.85 + lvl * 0.55)
        og = QRadialGradient(cx - orb_r * 0.25, cy - orb_r * 0.25, orb_r * 1.5)
        hi = QColor(c1)
        hi.setAlpha(255)
        og.setColorAt(0.0, hi)
        lo = QColor(c2)
        lo.setAlpha(220)
        og.setColorAt(1.0, lo)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(og)
        p.drawEllipse(QPointF(cx, cy), orb_r, orb_r)
        p.end()


# ── Chat bubbles ────────────────────────────────────────────────────
class Bubble(QFrame):
    def __init__(self, role: str, text: str):
        super().__init__()
        self.setObjectName(role)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if role in ("tool", "toolresult"):
            label.setFont(QFont("Cascadia Code, Consolas, monospace", 9))
        lay.addWidget(label)
        self.label = label


class ChatView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._host.setObjectName("chathost")
        self._col = QVBoxLayout(self._host)
        self._col.setContentsMargins(18, 14, 18, 14)
        self._col.setSpacing(10)
        self._col.addStretch(1)
        self.setWidget(self._host)

    def add(self, role: str, text: str):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        bubble = Bubble(role, text)
        if role == "user":
            bubble.setMaximumWidth(600)
            row.addStretch(1)
            row.addWidget(bubble)
        elif role in ("tool", "toolresult", "system"):
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            bubble.setMaximumWidth(660)
            row.addWidget(bubble)
            row.addStretch(1)
        self._col.insertLayout(self._col.count() - 1, row)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


# ── Confirmation dialog ─────────────────────────────────────────────
class ConfirmDialog(QDialog):
    def __init__(self, tool: str, args: dict, reason: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("eleon — confirm action")
        self.setModal(True)
        self.setMinimumWidth(470)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)

        title = QLabel(f"⚠  Confirm: {tool}")
        title.setStyleSheet(f"color:{DANGER};font-size:16px;font-weight:700;")
        lay.addWidget(title)

        why = QLabel(reason)
        why.setStyleSheet(f"color:{TXT};font-size:13px;")
        why.setWordWrap(True)
        lay.addWidget(why)

        if args:
            a = QLabel(_fmt_args(args))
            a.setWordWrap(True)
            a.setFont(QFont("Cascadia Code, Consolas, monospace", 9))
            a.setStyleSheet(
                f"color:{DIM};background:{BG};border:1px solid {BORDER};"
                "border-radius:8px;padding:10px;")
            lay.addWidget(a)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        approve = QPushButton("Approve")
        approve.setObjectName("danger")
        approve.clicked.connect(self.accept)
        approve.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(approve)
        lay.addLayout(btns)


# ── Main window ─────────────────────────────────────────────────────
class EleonWindow(QWidget):
    def __init__(self, worker: AgentWorker, autostart_voice: bool = True):
        super().__init__()
        self.worker = worker
        self.setWindowTitle(f"{ASSISTANT_NAME} — desktop agent")
        self.resize(860, 860)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 14, 16, 14)
        name = QLabel(ASSISTANT_NAME)
        name.setObjectName("wordmark")
        self.model = QLabel("")
        self.model.setObjectName("modeltag")
        self.speak_toggle = QPushButton("🔊 Speak")
        self.speak_toggle.setObjectName("speaktoggle")
        self.speak_toggle.setCheckable(True)
        self.speak_toggle.setToolTip("Speak eleon's replies aloud")
        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self._on_reset)
        h.addWidget(name)
        h.addStretch(1)
        h.addWidget(self.model)
        h.addSpacing(10)
        h.addWidget(self.speak_toggle)
        h.addWidget(reset_btn)
        root.addWidget(header)

        # Voice stage (the hero orb)
        stage = QFrame()
        stage.setObjectName("stage")
        stage.setMaximumHeight(210)
        s = QVBoxLayout(stage)
        s.setContentsMargins(0, 12, 0, 8)
        s.setSpacing(4)
        self.orb = VoiceOrb()
        self.caption = QLabel("")
        self.caption.setObjectName("caption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        s.addWidget(self.orb, 1)
        s.addWidget(self.caption)
        root.addWidget(stage)

        # Transcript
        self.chat = ChatView()
        root.addWidget(self.chat, 1)

        # Input bar
        bar = QFrame()
        bar.setObjectName("inputbar")
        b = QHBoxLayout(bar)
        b.setContentsMargins(16, 12, 16, 16)
        self.mic = QPushButton("🎙")
        self.mic.setObjectName("mic")
        self.mic.setCheckable(True)
        self.mic.setChecked(True)
        self.mic.setToolTip("Mute / unmute the microphone")
        self.mic.clicked.connect(self._on_mic_toggle)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            f"Say “{WAKE_WORD} …” or type a command")
        self.input.returnPressed.connect(self._on_send)
        self.send = QPushButton("Send")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self._on_send)
        b.addWidget(self.mic)
        b.addWidget(self.input, 1)
        b.addWidget(self.send)
        root.addWidget(bar)

        # Wire agent worker → GUI
        worker.event_signal.connect(self._on_event)
        worker.reply_signal.connect(self._on_reply)
        worker.error_signal.connect(self._on_error)
        worker.busy_signal.connect(self._on_busy)
        worker.status_signal.connect(self._on_status)
        worker.confirm_request.connect(self._on_confirm)

        # Voice state
        self._speakers: set = set()
        self._muted = False
        self._speaking = False
        self.engine: VoiceEngine | None = None
        self._voice_ok = self._check_voice()

        if not self._voice_ok:
            for wdg in (self.mic, self.speak_toggle):
                wdg.setEnabled(False)
                wdg.setToolTip("Install sounddevice + edge-tts for voice")
            self.orb.set_mode("idle")
            self.caption.setText("voice unavailable")
        elif autostart_voice:
            self._start_engine()

        self.chat.add("assistant",
                      f"Hey {USER_NAME}. I'm listening — say “{WAKE_WORD}” "
                      "then your command, or just type below.")

    # ── Voice engine lifecycle ──
    @staticmethod
    def _check_voice() -> bool:
        try:
            import sounddevice  # noqa: F401
            import edge_tts     # noqa: F401
            return True
        except Exception:
            return False

    def _start_engine(self):
        self.engine = VoiceEngine()
        self.engine.level.connect(self.orb.set_level)
        self.engine.heard.connect(self._on_voice_heard)
        self.engine.stt.connect(lambda: self._set_state("processing"))
        self.engine.failed.connect(self._on_engine_failed)
        self.engine.start()
        self._set_state("listening")

    def _on_engine_failed(self, msg: str):
        self.chat.add("system", f"microphone unavailable: {msg}")
        self._voice_ok = False
        self.mic.setEnabled(False)
        self._set_state("idle")

    def _set_state(self, state: str):
        captions = {"listening": "listening…", "processing": "thinking…",
                    "speaking": "speaking…", "muted": "microphone muted",
                    "idle": ""}
        self.orb.set_mode("listening" if state == "listening" else
                          "processing" if state == "processing" else
                          "speaking" if state == "speaking" else "idle")
        self.caption.setText(captions.get(state, ""))

    def _resume_listening(self):
        if self.engine and not self._muted:
            self.engine.resume()
            self._set_state("listening")
        elif self._muted:
            self._set_state("muted")
        else:
            self._set_state("idle")

    # ── Input ──
    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._begin_turn(text, spoken=False)

    def _on_reset(self):
        self.worker.reset()

    def _on_mic_toggle(self):
        self._muted = not self.mic.isChecked()
        if self.engine:
            if self._muted:
                self.engine.pause()
                self.mic.setText("🔇")
                self._set_state("muted")
            else:
                self.mic.setText("🎙")
                self._resume_listening()

    def _on_voice_heard(self, text: str):
        text = (text or "").strip()
        if not text:
            self._resume_listening()
            return
        low = text.lower()
        if WAKE_WORD not in low:          # not addressed to eleon → keep listening
            self._resume_listening()
            return
        command = text[low.find(WAKE_WORD) + len(WAKE_WORD):].strip(" ,.:!?-")
        if not command:
            self._resume_listening()
            return
        self._begin_turn(command, spoken=True)

    def _begin_turn(self, text: str, spoken: bool):
        self.chat.add("user", ("🎙 " if spoken else "") + text)
        if self.engine:
            self.engine.pause()           # don't listen while we work / speak
        self._set_state("processing")
        self.worker.submit(text)

    # ── Voice output ──
    def _speak(self, text: str):
        w = SpeakWorker(text)
        self._speakers.add(w)

        def done():
            self._speakers.discard(w)
            self._speaking = False
            self._resume_listening()

        w.finished.connect(done)
        self._speaking = True
        self._set_state("speaking")
        w.start()

    # ── Agent signal slots ──
    def _on_event(self, kind: str, data: dict):
        if kind == "tool_call":
            self.chat.add("tool", f"→ {data['name']}({_fmt_args(data['args'])})")
        elif kind == "tool_result":
            self.chat.add("toolresult",
                          str(data.get("result", "")).replace("\n", " ")[:160])
        elif kind == "system":
            self.chat.add("system", data.get("text", ""))
        elif kind == "maxsteps":
            self.chat.add("system", "step limit reached — wrapping up")

    def _on_reply(self, text: str):
        self.chat.add("assistant", text)
        if self._voice_ok and self.speak_toggle.isChecked():
            self._speak(text)  # keeps engine paused until speech ends

    def _on_error(self, text: str):
        self.chat.add("error", f"[error] {text}")

    def _on_status(self, label: str):
        self.model.setText(label)

    def _on_busy(self, busy: bool):
        self.send.setEnabled(not busy)
        self.input.setEnabled(not busy)
        if busy:
            self._set_state("processing")
        else:
            self.input.setFocus()
            if not self._speaking:        # if a reply is being spoken, wait
                self._resume_listening()

    def _on_confirm(self, tool: str, args: dict, reason: str):
        dlg = ConfirmDialog(tool, args, reason, self)
        approved = dlg.exec() == QDialog.DialogCode.Accepted
        self.worker.resolve_confirm(approved)
        self.chat.add("system",
                      f"{tool}: {'approved' if approved else 'cancelled'}")

    def closeEvent(self, ev):
        if self.engine is not None:
            self.engine.stop_engine()
            self.engine.wait(1500)
        for w in list(self._speakers):
            w.wait(1500)
        self.worker.stop()
        self.worker.wait(2000)
        ev.accept()


STYLESHEET = f"""
* {{ font-family: 'Segoe UI', sans-serif; color: {TXT}; }}
QWidget {{ background: {BG}; }}
#header {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
#stage {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
          stop:0 {PANEL}, stop:1 {BG}); border-bottom: 1px solid {BORDER}; }}
#inputbar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
#chathost {{ background: {BG}; }}
QScrollArea {{ background: {BG}; border: none; }}
QLabel {{ background: transparent; }}

#wordmark {{ color: {ACCENT}; font-size: 19px; font-weight: 800;
             letter-spacing: 2px; }}
#modeltag {{ color: {DIM}; font-size: 11px; }}
#caption {{ color: {DIM}; font-size: 12px; letter-spacing: 1px; }}

QFrame#user {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
               stop:0 {USERC}, stop:1 {ACCENT2}); border-radius: 16px; }}
QFrame#user QLabel {{ color: white; font-size: 13px; }}
QFrame#assistant {{ background: {ELEV}; border: 1px solid {BORDER};
                    border-radius: 16px; }}
QFrame#assistant QLabel {{ font-size: 13px; }}
QFrame#error {{ background: #2d1618; border: 1px solid #5c2b2b;
               border-radius: 16px; }}
QFrame#error QLabel {{ color: #ff9b9b; font-size: 13px; }}
QFrame#tool, QFrame#toolresult, QFrame#system {{ background: transparent; }}
QFrame#tool QLabel {{ color: {ACCENT}; }}
QFrame#toolresult QLabel {{ color: {DIM}; }}
QFrame#system QLabel {{ color: {DIM}; font-style: italic; font-size: 11px; }}

QLineEdit {{ background: {BG}; border: 1px solid {BORDER};
             border-radius: 12px; padding: 11px 14px; font-size: 13px; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled {{ color: {DIM}; }}

QPushButton {{ border-radius: 12px; padding: 10px 16px; font-size: 13px;
               background: {ELEV}; }}
QPushButton:hover {{ background: #222c3b; }}
QPushButton#primary {{ background: {ACCENT}; color: #052824; font-weight: 700; }}
QPushButton#primary:hover {{ background: #5cf0dd; }}
QPushButton#primary:disabled {{ background: {ELEV}; color: {DIM}; }}
QPushButton#ghost {{ background: transparent; color: {DIM}; padding: 7px 12px; }}
QPushButton#ghost:hover {{ background: {ELEV}; color: {TXT}; }}
QPushButton#mic {{ background: {ELEV}; border-radius: 12px; padding: 10px 13px;
                   font-size: 15px; }}
QPushButton#mic:hover {{ background: #222c3b; }}
QPushButton#mic:checked {{ background: {ACCENT}; color: #052824; }}
QPushButton#mic:disabled {{ color: {DIM}; }}
QPushButton#speaktoggle {{ background: transparent; color: {DIM};
                           padding: 7px 12px; }}
QPushButton#speaktoggle:hover {{ background: {ELEV}; color: {TXT}; }}
QPushButton#speaktoggle:checked {{ background: {ACCENT2}; color: white;
                                   font-weight: 600; }}
QPushButton#speaktoggle:disabled {{ color: #4a5058; }}
QPushButton#danger {{ background: {DANGER}; color: #2b1402; font-weight: 700; }}
QPushButton#danger:hover {{ background: #ffb072; }}

QDialog {{ background: {PANEL}; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px;
                               min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #2f3a4a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def launch() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    worker = AgentWorker()
    win = EleonWindow(worker, autostart_voice=True)
    worker.start()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(launch())
