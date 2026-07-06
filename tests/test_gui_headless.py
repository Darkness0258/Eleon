"""
Headless GUI smoke test (offscreen Qt — no real window needed).
Run:  python tests/test_gui_headless.py

Verifies:
  1. The confirm dialog resolves Approve/Cancel correctly.
  2. The full worker path works: submit a benign prompt on the GUI thread →
     the agent runs on its worker thread → reply_signal lands back on the GUI
     thread. (Hits the network via the real brain; benign, no tools.)
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication, QDialog as _QDialog  # noqa: E402
from PyQt6.QtCore import QTimer, QThread            # noqa: E402
from PyQt6.QtGui import QPixmap                     # noqa: E402

from ui.gui import (AgentWorker, EleonWindow, ConfirmDialog,  # noqa: E402
                    VoiceOrb, VoiceEngine)


def main() -> int:
    app = QApplication([])

    # 1. Confirm dialog — auto-accept and auto-reject.
    d1 = ConfirmDialog("delete_path", {"path": "x"}, "gated action")
    QTimer.singleShot(0, d1.accept)
    accepted = d1.exec() == _QDialog.DialogCode.Accepted

    d2 = ConfirmDialog("shutdown", {}, "gated action")
    QTimer.singleShot(0, d2.reject)
    cancelled = d2.exec() == _QDialog.DialogCode.Rejected

    print(f"[gui] confirm approve={accepted} cancel={cancelled}")
    assert accepted and cancelled, "confirm dialog result mapping is wrong"

    # 1b. Voice-reactive orb paints in every mode without crashing.
    assert issubclass(VoiceEngine, QThread)
    orb = VoiceOrb()
    orb.resize(180, 180)
    for m in ("listening", "processing", "speaking", "idle"):
        orb.set_mode(m)
        orb.set_level(0.7)
        orb._tick()
        pm = QPixmap(orb.size())
        orb.render(pm)  # exercises paintEvent
    print("[gui] orb renders in all modes: OK")

    # 2. Worker end-to-end with a benign, tool-free prompt. autostart_voice=
    #    False so the headless test never opens the microphone.
    worker = AgentWorker()
    win = EleonWindow(worker, autostart_voice=False)
    got: dict = {}

    def done_reply(t):
        got["reply"] = t
        app.quit()

    def done_error(t):
        got["error"] = t
        app.quit()

    worker.reply_signal.connect(done_reply)
    worker.error_signal.connect(done_error)
    worker.start()

    QTimer.singleShot(600, lambda: worker.submit("Reply with exactly one word: pong"))
    QTimer.singleShot(60000, app.quit)  # hard safety timeout
    app.exec()

    worker.stop()
    worker.wait(3000)

    print(f"[gui] worker reply: {got.get('reply')!r}")
    if got.get("error"):
        print(f"[gui] worker error: {got['error']}")

    # 3. Confirm bridge (deterministic, no LLM): drive guard.confirm directly
    #    on the worker's asyncio loop and resolve it from the GUI thread. This
    #    isolates the exact cross-thread park/resume mechanism the gated-action
    #    flow depends on, without relying on the model choosing to call a tool.
    import asyncio
    import time
    from PyQt6.QtCore import QCoreApplication

    w2 = AgentWorker()
    seen: dict = {}

    def on_confirm(tool, args, reason):
        seen["req"] = (tool, reason)
        w2.resolve_confirm(True)  # approve

    w2.confirm_request.connect(on_confirm)
    w2.start()
    for _ in range(60):  # wait for the worker loop to come up
        if w2._loop is not None:
            break
        time.sleep(0.05)

    fut = asyncio.run_coroutine_threadsafe(
        w2._confirm("empty_recycle_bin", {"scope": "all"}, "gated action"),
        w2._loop)
    result = None
    for _ in range(300):  # pump Qt events so the on_confirm slot can run
        if fut.done():
            result = fut.result()
            break
        QCoreApplication.processEvents()
        time.sleep(0.02)
    w2.stop()
    w2.wait(3000)
    print(f"[gui] confirm bridge: request={seen.get('req')} result={result}")

    ok = (bool(got.get("reply")) and result is True and bool(seen.get("req")))
    print(f"\n[gui] {'PASS' if ok else 'FAIL'}: worker delivered a reply AND "
          f"the async confirm bridge parked/resumed across threads")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
