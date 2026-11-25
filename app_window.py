"""Main window of LearnerOnPC (demo): shows saved sessions and answers questions about them."""
from __future__ import annotations

import base64
import html
import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QGraphicsBlurEffect, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QSplitter, QTabWidget, QTextBrowser, QTextEdit, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

import ai_engine
import master
import search
from config import APP_NAME, APP_VERSION, DEMO_NOTICE, Settings, resource_path
from store import Session, fmt_time, is_outside, unmark_outside

ASSETS = resource_path("assets")

QSS = """
QMainWindow, QWidget { background:#11161f; color:#e8eaf0; font-family:"Segoe UI"; font-size:13px; }
QToolBar { background:#151c28; border:0; border-bottom:1px solid #2c3647; spacing:6px; padding:6px; }
QToolButton { background:#1d2636; border:1px solid #2c3647; border-radius:8px; padding:6px 12px; }
QToolButton:hover { border-color:#e0a050; } QToolButton:disabled { color:#5b6479; }
QPushButton { background:#1d2636; border:1px solid #2c3647; border-radius:8px; padding:7px 14px; }
QPushButton:hover { border-color:#e0a050; } QPushButton:disabled { color:#5b6479; }
QTabWidget::pane { border:0; } QTabBar::tab { background:#11161f; color:#8d95a6; padding:9px 18px; border:0; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#e8eaf0; border-bottom:2px solid #e0a050; } QTabBar::tab:hover { color:#e8eaf0; }
QTextBrowser, QTextEdit, QListWidget, QLineEdit
    { background:#141b27; border:1px solid #2c3647; border-radius:8px; padding:6px; selection-background-color:#5a4520; }
QListWidget::item { padding:6px; border-radius:6px; } QListWidget::item:selected { background:#3a2f1c; }
QStatusBar { background:#151c28; border-top:1px solid #2c3647; } QStatusBar QLabel { color:#8d95a6; }
QProgressBar { background:#1d2636; border:0; border-radius:4px; height:8px; text-align:center; }
QProgressBar::chunk { background:#e0a050; border-radius:4px; }
QLabel#title { font-size:15px; font-weight:600; }
QLabel#why { background:#2a2214; border:1px solid #6b5124; border-radius:8px; padding:8px; color:#f0d9a8; }
QMenu { background:#1a2130; border:1px solid #2c3647; } QMenu::item { padding:7px 22px; } QMenu::item:selected { background:#3a2f1c; }
"""

DOC_CSS = ("<style>body{color:#e8eaf0;font-family:'Segoe UI';font-size:13px} h3{color:#e0a050;margin:14px 0 2px;font-size:14px}"
           " ul{margin-top:2px} li{margin:3px 0} .m{color:#8d95a6}</style>")

BLUR_WHY = ("Blurred on purpose. These demo sessions were recorded from videos made by other people, so their spoken "
            "words and screen pictures belong to those creators and are not shown here. The notes, mind map, graph and "
            "study pack are the app's own study material made from them; any note that repeated a video word for word "
            "is hidden too.")


def demo_message(parent, what: str) -> None:
    QMessageBox.information(
        parent, "Not available in this demo",
        f"{what} is not possible in {APP_NAME}.\n\nThis is a demo app with limited features, made to give an idea of "
        "what the full app can do. It shows sessions that were already made and answers questions about them "
        "on the Ask tab.")


class ActionPage(QWebEnginePage):
    """Lets a local page talk back to the app with links like ll://open/<id>."""

    def __init__(self, parent, callback) -> None:
        super().__init__(parent)
        self._cb = callback

    def acceptNavigationRequest(self, url, nav_type, is_main_frame) -> bool:
        if url.scheme() == "ll":
            name, args = url.host(), [p for p in url.path().split("/") if p]
            QTimer.singleShot(0, lambda: self._cb(name, args))
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class WebPanel(QWebEngineView):
    """A local HTML page that exposes a render(data) function."""

    def __init__(self, filename: str, on_action=None) -> None:
        super().__init__()
        self._ready, self._pending = False, None
        if on_action is not None:
            self.setPage(ActionPage(self, on_action))
        self.page().setBackgroundColor(QColor("#11161f"))
        self.loadFinished.connect(self._loaded)
        self.setUrl(QUrl.fromLocalFile(str(ASSETS / filename)))

    def _loaded(self, ok: bool) -> None:
        self._ready = ok
        if ok and self._pending is not None:
            self.page().runJavaScript(f"render({json.dumps(self._pending)})")

    def push(self, data) -> None:
        self._pending = data
        if self._ready:
            self.page().runJavaScript(f"render({json.dumps(data)})")

    def call(self, js: str, callback) -> None:
        self.page().runJavaScript(js, callback)


class SessionsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Demo sessions"); self.setMinimumSize(520, 340)
        self.chosen: str | None = None
        self.list = QListWidget()
        for m in Session.list_sessions():
            it = QListWidgetItem(f"{m['title']}\n{time.strftime('%d %b %Y', time.localtime(m['created']))} - "
                                 f"{m['segments']} transcript lines (blurred)")
            it.setData(Qt.ItemDataRole.UserRole, m["id"]); self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda _: self._open())
        b = QPushButton("Open"); b.clicked.connect(self._open)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(b)
        lay = QVBoxLayout(self); lay.addWidget(self.list); lay.addLayout(row)

    def _open(self) -> None:
        it = self.list.currentItem()
        if it:
            self.chosen = it.data(Qt.ItemDataRole.UserRole); self.accept()


class ModelDialog(QDialog):
    """Downloads the one AI model (and its server program) for the Ask tab."""
    progress = Signal(float, str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI model for Ask"); self.setMinimumWidth(520)
        ram = ai_engine.ram_gb()
        low = 0 < ram < ai_engine.MIN_RAM_GB
        info = QLabel(
            "<b>Ask needs one AI model on this PC.</b><br>"
            f"{ai_engine.MODEL_LABEL}, open-source (Apache 2.0), plus the llama.cpp server program (about 25 MB). "
            "It is downloaded once and then works offline. Nothing you type leaves this PC."
            + (f"<br><br><span style='color:#ff8a8a'>This PC has {ram:.1f} GB of memory; the model needs about "
               f"{ai_engine.MIN_RAM_GB} GB, so answers may be very slow.</span>" if low else ""))
        info.setWordWrap(True); info.setTextFormat(Qt.TextFormat.RichText)
        self.bar = QProgressBar(); self.bar.setRange(0, 100)
        self.msg = QLabel(""); self.msg.setWordWrap(True)
        self.go = QPushButton("Download"); self.go.clicked.connect(self._start)
        close = QPushButton("Not now"); close.clicked.connect(self.reject)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(close); row.addWidget(self.go)
        lay = QVBoxLayout(self)
        for w in (info, self.bar, self.msg):
            lay.addWidget(w)
        lay.addLayout(row)
        self.progress.connect(lambda f, t: (self.bar.setValue(int(f * 100)), self.msg.setText(t)))
        self.finished_ok.connect(self.accept)
        self.failed.connect(self._failed)

    def _start(self) -> None:
        self.go.setEnabled(False)

        def work():
            try:
                ai_engine.download(lambda f, t: self.progress.emit(f, t))
                self.finished_ok.emit()
            except Exception as e:
                self.failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _failed(self, err: str) -> None:
        self.msg.setStyleSheet("color:#ff8a8a"); self.msg.setText(err)
        self.go.setText("Try again"); self.go.setEnabled(True)


class MainWindow(QMainWindow):
    answer_ready = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.server = ai_engine.Server()
        self._ask_id = 0
        self._asking = False
        self.highlight_lines: set[int] = set()
        self.highlight_note: tuple | None = None
        self.answer_ready.connect(self._on_answer)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} - demo")
        self.resize(1320, 820)
        self.setStyleSheet(QSS)
        self.session = self._first_session()
        self._build_toolbar()
        self._build_tabs()
        self.status = QLabel("")
        self.statusBar().addWidget(self.status, 1)
        self.refresh_all()
        QTimer.singleShot(300, self.show_welcome)
        threading.Thread(target=search.ensure_all, daemon=True).start()

    @staticmethod
    def _first_session() -> Session:
        rows = Session.list_sessions()
        if rows:
            try:
                return Session.load(rows[0]["id"])
            except (OSError, ValueError):
                pass
        return Session("No demo session found")

    # ================================================================ building UI
    def _build_toolbar(self) -> None:
        tb = QToolBar("Main"); tb.setMovable(False); tb.setIconSize(QSize(18, 18)); self.addToolBar(tb)
        acts = [("▶  Start recording", lambda: demo_message(self, "Recording a new session")),
                ("Analyse a file...", lambda: demo_message(self, "Analysing a video or audio file")),
                ("Sessions...", self.on_sessions),
                ("Generate analysis", lambda: demo_message(self, "Generating a new analysis")),
                ("About this demo", self.show_welcome)]
        for label, fn in acts:
            a = QAction(label, self); a.triggered.connect(fn); tb.addAction(a)
        tb.addSeparator()
        self.title_lbl = QLabel(""); self.title_lbl.setObjectName("title"); tb.addWidget(self.title_lbl)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        exp = QToolButton(); exp.setText("Export"); exp.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(exp)
        menu.addAction("Notes + study pack (Markdown)...", self.export_md)
        menu.addAction("Mind map (SVG)...", self.export_svg)
        menu.addAction("3D graph (PNG)...", self.export_png)
        exp.setMenu(menu); tb.addWidget(exp)

    @staticmethod
    def _why_label() -> QLabel:
        w = QLabel(BLUR_WHY); w.setObjectName("why"); w.setWordWrap(True)
        return w

    def _build_tabs(self) -> None:
        self.tabs = QTabWidget(); self.setCentralWidget(self.tabs)

        # Notes (with the transcript, blurred)
        self.transcript = QTextBrowser(); self.transcript.setOpenLinks(False)
        blur = QGraphicsBlurEffect(self.transcript); blur.setBlurRadius(9); self.transcript.setGraphicsEffect(blur)
        self.notes_view = QTextBrowser(); self.notes_view.setOpenLinks(False)
        self.notes_view.anchorClicked.connect(self.on_note_click)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(10, 10, 5, 10)
        t1 = QLabel("Transcript"); t1.setObjectName("title"); ll.addWidget(t1)
        ll.addWidget(self._why_label()); ll.addWidget(self.transcript)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(5, 10, 10, 10)
        t2 = QLabel("Notes"); t2.setObjectName("title"); rl.addWidget(t2)
        hint = QLabel("Click a note's title or a point to see which transcript lines it came from (they light up, still blurred).")
        hint.setWordWrap(True); hint.setStyleSheet("color:#8d95a6"); rl.addWidget(hint)
        rl.addWidget(self.notes_view)
        split.addWidget(left); split.addWidget(right); split.setSizes([520, 780])
        self.tabs.addTab(split, "Notes")

        self.mindmap = WebPanel("mindmap.html")
        self.tabs.addTab(self._panel(self.mindmap, [("Rebuild with AI", lambda: demo_message(self, "Rebuilding the mind map")),
                                                    ("Export SVG", self.export_svg)]), "Mind map")
        self.graph = WebPanel("graph3d.html")
        self.tabs.addTab(self._panel(self.graph, [("Export PNG", self.export_png)]), "3D graph")
        self.pack = WebPanel("studypack.html")
        self.tabs.addTab(self._panel(self.pack, [("Generate analysis", lambda: demo_message(self, "Generating a new analysis"))]),
                         "Study pack")

        # Screen (blurred)
        self.screen_list = QListWidget(); self.screen_list.setIconSize(QSize(128, 72)); self.screen_list.setMaximumWidth(360)
        self.screen_img = QLabel(); self.screen_img.setAlignment(Qt.AlignmentFlag.AlignCenter); self.screen_img.setMinimumHeight(260)
        sd = QWidget(); sdl = QVBoxLayout(sd); sdl.addWidget(self._why_label()); sdl.addWidget(self.screen_img, 1)
        ss = QSplitter(Qt.Orientation.Horizontal); ss.addWidget(self.screen_list); ss.addWidget(sd); ss.setSizes([340, 900])
        self.screen_list.currentRowChanged.connect(self.on_screen_selected)
        self.tabs.addTab(ss, "Screen")

        # Ask
        self.chat = QTextBrowser()
        self.chat_in = QLineEdit(); self.chat_in.setPlaceholderText("Ask a question about the demo sessions...")
        self.chat_in.returnPressed.connect(self.on_ask)
        self.ask_btn = QPushButton("Ask"); self.ask_btn.clicked.connect(self.on_ask)
        stop = QPushButton("Stop"); stop.clicked.connect(lambda: self.stop_ask("Stopped."))
        aw = QWidget(); al = QVBoxLayout(aw)
        note = QLabel("Answers come from the demo sessions' notes, using one AI model on this PC. Leaving this tab or "
                      "pressing Stop closes the model and frees the memory it used.")
        note.setWordWrap(True); note.setStyleSheet("color:#8d95a6"); al.addWidget(note); al.addWidget(self.chat)
        row = QHBoxLayout(); row.addWidget(self.chat_in); row.addWidget(self.ask_btn); row.addWidget(stop)
        al.addLayout(row)
        self.tabs.addTab(aw, "Ask")
        self.ask_tab = aw

        self.master_graph = WebPanel("master3d.html", on_action=self.on_web_action)
        self.tabs.addTab(self._panel(self.master_graph, [("Refresh", self.refresh_master)]), "Master graph")
        self.library = WebPanel("library.html", on_action=self.on_web_action)
        self.tabs.addTab(self.library, "Library")
        self._last_tab = None
        self.tabs.currentChanged.connect(self.on_tab_changed)

    @staticmethod
    def _panel(view: QWidget, buttons: list) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout(); row.setContentsMargins(10, 8, 10, 0)
        for label, fn in buttons:
            b = QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(); lay.addLayout(row); lay.addWidget(view)
        return w

    # ================================================================ dialogs
    def show_welcome(self) -> None:
        QMessageBox.information(
            self, f"Welcome to {APP_NAME}",
            f"{APP_NAME} {APP_VERSION} - demo\n\n{DEMO_NOTICE}\n\n"
            "What you can try:\n"
            "- Notes, Mind map, 3D graph and Study pack of three saved sessions\n"
            "- Master graph and Library: all sessions together\n"
            "- Ask: questions answered from the sessions' notes by an AI model on this PC (a one-time download)\n\n"
            "The transcripts and screen pictures are blurred on purpose: the sessions were recorded from other "
            "people's videos, and their words and pictures belong to them.")

    def on_sessions(self) -> None:
        dlg = SessionsDialog(self)
        if dlg.exec() and dlg.chosen:
            self.open_session(dlg.chosen)

    def open_session(self, sid: str) -> None:
        try:
            self.session = Session.load(sid)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Open session", f"Could not open that session: {e}"); return
        self.highlight_lines, self.highlight_note = set(), None
        self.refresh_all()

    # ================================================================ views
    def refresh_all(self) -> None:
        s = self.session
        self.title_lbl.setText(s.title)
        self.render_transcript(); self.render_notes()
        self.graph.push(s.graph_data() if s.nodes else {"nodes": [], "edges": [], "duration": s.duration()})
        self.mindmap.push(s.mindmap or {"name": "", "children": []})
        self.push_pack()
        self.screen_list.clear(); self.screen_img.clear()
        for item in s.screens:
            it = QListWidgetItem(f"[{fmt_time(item['t'])}] screen picture (blurred)")
            img = s.dir / item["image"] if item.get("image") else None
            if img and img.exists():
                it.setIcon(QIcon(QPixmap(str(img)).scaled(128, 72, Qt.AspectRatioMode.KeepAspectRatio)))
            self.screen_list.addItem(it)
        self.status.setText(f"{s.title} - {len(s.notes)} notes, {len(s.nodes)} concepts")

    def render_transcript(self) -> None:
        lines = []
        for i, seg in enumerate(self.session.segments):
            text = html.escape(seg["text"])
            line = (f"<a name='L{i}'></a><span style='color:#6b7385'>[{fmt_time(seg['start'])}] {text}</span>" if seg.get("ad")
                    else f"<a name='L{i}'></a><span style='color:#e0a050'>[{fmt_time(seg['start'])}]</span> {text}")
            if i in self.highlight_lines:
                line = f"<span style='background-color:#5a4520'>{line}</span>"
            lines.append(line)
        self.transcript.setHtml("<br>".join(lines))

    def render_notes(self) -> None:
        s = self.session
        parts = [DOC_CSS]
        if s.topic:
            parts.append(f"<p class='m'>Topic: <b style='color:#e8eaf0'>{html.escape(s.topic)}</b></p>")
        if not s.notes:
            parts.append("<p class='m'>This session has no notes (it was made while the note writer was still being "
                         "built). Its concepts are in the 3D graph.</p>")
        for ni, n in enumerate(s.notes):
            parts.append(f"<h3><span class='m'>[{fmt_time(n['t'])}]</span> <a href='note:{ni}' style='color:inherit'>"
                         f"{html.escape(n['title'])}</a></h3><ul>"
                         + "".join(self._point_html(ni, pi, p) for pi, p in enumerate(n["points"])) + "</ul>")
        review = sorted(((nd["label"], unmark_outside(nd["definition"])) for nd in s.nodes.values()
                         if is_outside(nd.get("definition"))), key=lambda x: x[0].lower())
        if review:
            parts.append("<h3>Definitions to review</h3><p class='m'>Not given in the video: the AI added these from "
                         "general knowledge.</p><ul>" + "".join(f"<li><b>{html.escape(a)}</b>: {html.escape(b)}</li>"
                                                                for a, b in review) + "</ul>")
        if s.audit:
            parts.append("<h3>Dropped and changed</h3><p class='m'>What the checks left out or changed, and why.</p><ul>"
                         + "".join(f"<li><span class='m'>[{fmt_time(a['t'])}]</span> {html.escape(str(a.get('msg', '')))}</li>"
                                   for a in s.audit[-200:]) + "</ul>")
        self.notes_view.setHtml("".join(parts))

    @staticmethod
    def _point_html(ni: int, pi: int, p: str) -> str:
        if str(p).startswith("[Hidden"):
            return ("<li><span style='color:#6b7385'><i>Hidden: this point repeated the video word for word, so it is "
                    "not shown (the words belong to the video's creator).</i></span></li>")
        return f"<li><a href='pt:{ni}:{pi}' style='color:inherit;text-decoration:none'>{html.escape(p)}</a></li>"

    def on_note_click(self, url) -> None:
        parts = url.toString().split(":")
        if parts[0] not in ("note", "pt"):
            return
        try:
            ni = int(parts[1]); pi = int(parts[2]) if parts[0] == "pt" else None
            srcs = self.session.notes[ni].get("sources")
        except (ValueError, IndexError):
            return
        if not srcs:
            self.status.setText("The transcript lines of this note were not recorded."); return
        lines = sorted({x for src in srcs for x in src}) if pi is None else sorted(srcs[pi]) if pi < len(srcs) else []
        same = self.highlight_note == (ni, pi)
        self.highlight_note = None if same else (ni, pi)
        self.highlight_lines = set() if same or not lines else set(lines)
        self.render_transcript()
        if self.highlight_lines:
            self.transcript.scrollToAnchor(f"L{lines[0]}")
            self.status.setText(f"{len(lines)} transcript line(s) lit up (blurred). Click again to clear.")

    def push_pack(self) -> None:
        s = self.session
        self.pack.push({"title": s.title, "pack": dict(s.pack), "analytics": s.analytics() if s.nodes else {},
                        "chapters": s.chapters(), "mode": s.mode, "meeting": s.meeting,
                        "status": {**s.pack_status(), "stale": False}})

    def on_screen_selected(self, row: int) -> None:
        if 0 <= row < len(self.session.screens):
            img = self.session.dir / self.session.screens[row].get("image", "")
            if img.is_file():
                pm = QPixmap(str(img))
                self.screen_img.setPixmap(pm.scaledToHeight(min(pm.height() * 2, 380), Qt.TransformationMode.SmoothTransformation))

    def refresh_master(self) -> None:
        try:
            self.master_graph.push(master.payload())
        except Exception as e:
            self.status.setText(f"Master graph: {e}")

    def refresh_library(self) -> None:
        try:
            self.library.push(master.analytics_payload())
        except Exception as e:
            self.status.setText(f"Library: {e}")

    def on_web_action(self, name: str, args: list) -> None:
        sid = args[0] if args else ""
        if name == "open" and sid:
            self.open_session(sid); self.tabs.setCurrentIndex(0)
        elif name == "focus" and sid:
            self.tabs.setCurrentWidget(self.master_graph.parentWidget())
            self.master_graph.push(master.payload(selected=[sid]))
        elif name == "delete":
            demo_message(self, "Deleting a session")
        elif name == "link":
            demo_message(self, "Linking a meeting")
        elif name in ("merge", "dismiss") and len(args) == 2:
            (master.accept_suggestion if name == "merge" else master.dismiss_suggestion)(args[0], args[1])
            self.refresh_master()

    def on_tab_changed(self, _i: int) -> None:
        w = self.tabs.currentWidget()
        if self._last_tab is self.ask_tab and w is not self.ask_tab:
            self.stop_ask("Left the Ask tab: the AI model was closed.")
        self._last_tab = w
        if w is self.master_graph.parentWidget():
            self.refresh_master()
        elif w is self.library:
            self.refresh_library()

    # ================================================================ Ask
    def on_ask(self) -> None:
        q = self.chat_in.text().strip()
        if not q or self._asking:
            return
        if not ai_engine.ready():
            if not ModelDialog(self).exec() or not ai_engine.ready():
                return
        self.chat_in.clear()
        self.chat.append(f"<p><b style='color:#e0a050'>You:</b> {html.escape(q)}</p>")
        self.chat.append("<p style='color:#8d95a6'>Thinking... (the first question also starts the AI model)</p>")
        self._asking = True
        self.ask_btn.setEnabled(False)
        self._ask_id += 1
        qid = self._ask_id

        def work():
            try:
                self.server.start()
                text = ai_engine.ask(self.server, q, search.search(q))
            except ai_engine.AIError as e:
                text = "" if str(e) == "stopped" else f"(Could not answer: {e})"
            except Exception as e:
                text = f"(Could not answer: {e})"
            self.answer_ready.emit(qid, text)
        threading.Thread(target=work, name="ask", daemon=True).start()

    def _on_answer(self, qid: int, text: str) -> None:
        if qid != self._ask_id:
            return
        self._asking = False
        self.ask_btn.setEnabled(True)
        if text:
            self.chat.append(f"<p><b style='color:#7fd1a8'>{APP_NAME}:</b> {html.escape(text).replace(chr(10), '<br>')}</p>")

    def stop_ask(self, why: str) -> None:
        was = self._asking or self.server.running()
        self._ask_id += 1
        self._asking = False
        self.ask_btn.setEnabled(True)
        threading.Thread(target=self.server.stop, daemon=True).start()
        if was:
            self.chat.append(f"<p style='color:#8d95a6'>{html.escape(why)}</p>")

    # ================================================================ export
    def _save_dialog(self, title: str, suffix: str, flt: str) -> str:
        safe = "".join(c for c in self.session.title if c.isalnum() or c in " -_")[:50].strip() or "session"
        return QFileDialog.getSaveFileName(self, title, f"{safe}{suffix}", flt)[0]

    def export_md(self) -> None:
        p = self._save_dialog("Export notes", ".md", "Markdown (*.md)")
        if p:
            Path(p).write_text(self.session.export_markdown(), encoding="utf-8"); self.status.setText(f"Exported {p}")

    def export_svg(self) -> None:
        p = self._save_dialog("Export mind map", "-mindmap.svg", "SVG (*.svg)")
        if p:
            self.mindmap.call("exportSVG()", lambda svg: svg and Path(p).write_text(svg, encoding="utf-8"))

    def export_png(self) -> None:
        p = self._save_dialog("Export graph image", "-graph.png", "PNG (*.png)")
        if p:
            self.graph.call("exportPNG()", lambda d: d and "," in d and Path(p).write_bytes(base64.b64decode(d.split(",", 1)[1])))

    def closeEvent(self, event) -> None:
        self.server.stop()
        event.accept()
