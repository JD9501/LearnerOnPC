"""Window tests for LearnerOnPC with a fake AI model. Run:  python tests/test_demo.py
(on a machine without a screen: set QT_QPA_PLATFORM=offscreen)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()

from PySide6.QtCore import QUrl                       # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

import ai_engine                                       # noqa: E402
import config                                          # noqa: E402
import search                                          # noqa: E402

shown: list[tuple[str, str]] = []
QMessageBox.information = staticmethod(lambda parent, title, text, *a, **k: shown.append((title, text)))


class FakeServer:
    def __init__(self):
        self.started = self.stopped = 0
        self.up = False

    def running(self):
        return self.up

    def start(self, timeout=0):
        self.started += 1
        self.up = True

    def stop(self):
        self.stopped += 1
        self.up = False

    def chat(self, system, user, json_mode=False, max_tokens=0):
        if json_mode:
            return '{"supported": [true, false]}'
        return "Early people farmed in the Fertile Crescent [Demo 01:00]. The moon is made of cheese [Demo 02:00]."


def pump(app, secs=0.3):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication(sys.argv)
    assert config.install_demo_sessions() == 3
    from app_window import MainWindow
    w = MainWindow()
    w.server = FakeServer()
    pump(app, 1.0)

    # 1. tabs, and the demo message for every action that would need the full app
    names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert names == ["Notes", "Mind map", "3D graph", "Study pack", "Screen", "Ask", "Master graph", "Library"], names
    acts = {a.text(): a for a in w.findChildren(type(w.menuBar().addAction("x")))}
    shown.clear()
    for label in ("▶  Start recording", "Analyse a file...", "Generate analysis"):
        acts[label].trigger()
    assert len(shown) == 3 and all(t == "Not available in this demo" for t, _ in shown), shown
    assert "demo app with limited features" in shown[0][1]
    shown.clear()
    w.on_web_action("delete", ["x"])
    assert shown and "Deleting" in shown[0][1]
    print("1. demo messages: OK")

    # 2. every session: transcript blurred (an effect on the widget + scrambled words), screens have no text
    from store import Session
    for m in Session.list_sessions():
        w.open_session(m["id"])
        s = w.session
        assert s.blurred and w.transcript.graphicsEffect() is not None
        assert all(not x["text"] for x in s.screens)
        assert w.title_lbl.text() == s.title
    print("2. blurred transcripts and screens: OK")

    # 3. notes: clicking a point lights up its transcript lines
    best = max(Session.list_sessions(), key=lambda m: len(Session.load(m["id"]).notes))
    w.open_session(best["id"])
    with_src = [i for i, n in enumerate(w.session.notes) if n.get("sources")]
    if with_src:
        w.on_note_click(QUrl(f"note:{with_src[0]}"))
        assert w.highlight_lines
        w.on_note_click(QUrl(f"note:{with_src[0]}"))
        assert not w.highlight_lines
        print("3. note click highlights lines: OK")
    else:
        print("3. note click: SKIPPED (no session records its source lines)")

    # 4. Ask: search uses notes only; unconfirmed sentences are dropped and reported; leaving the tab stops the model
    search.ensure_all()
    hits = search.search("civilization farming")
    assert hits and all(h["kind"] in ("note", "concept", "summary") for h in hits)
    ai_engine.ready = lambda: True
    w.tabs.setCurrentWidget(w.ask_tab)
    w.chat_in.setText("Where did farming start?")
    w.on_ask()
    end = time.monotonic() + 5
    while w._asking and time.monotonic() < end:
        pump(app, 0.05)
    text = w.chat.toPlainText()
    assert "Fertile Crescent" in text and "cheese" not in text and "left out" in text, text
    w.tabs.setCurrentIndex(0)
    pump(app, 0.3)
    assert w.server.stopped >= 1 and not w.server.up
    print("4. Ask with one model, check, stop on leaving the tab: OK")

    # 5. nothing from the full app's pipeline is shipped
    banned = ("pipeline", "scheduler", "grounding", "analysis", "engine", "bridge", "transcriber", "audio_capture",
              "screen_capture", "checkpoint", "classifier", "ads", "aside", "principles", "LearnLens")
    for f in ROOT.glob("*.py"):
        assert f.stem not in banned, f
        body = f.read_text(encoding="utf-8")
        assert "LearnLens" not in body, f
        for mod in banned[:-1]:
            assert f"import {mod}\n" not in body and f"from {mod} import" not in body, (f, mod)
    print("5. no recording, notes-writing or orchestration code in the demo: OK")
    w.close()
    print("ALL DEMO TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
