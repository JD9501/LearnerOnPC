"""LearnerOnPC - a demo of a local study companion for learning videos. Run:  python main.py"""
import multiprocessing
import os
import sys
import traceback


def _prepare_streams() -> None:
    """A windowed packaged app has no console: send printed output to a log file."""
    try:
        from config import log_file
        frozen = getattr(sys, "frozen", False) and "--selftest" not in sys.argv
        if frozen or sys.stdout is None or sys.stderr is None:
            f = open(log_file(), "a", encoding="utf-8", buffering=1)
            sys.stdout = f if (frozen or sys.stdout is None) else sys.stdout
            sys.stderr = f if (frozen or sys.stderr is None) else sys.stderr
    except Exception:
        devnull = open(os.devnull, "w")
        sys.stdout = sys.stdout or devnull
        sys.stderr = sys.stderr or devnull


def _crash_handler(exc_type, exc, tb) -> None:
    try:
        sys.stderr.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(None, "LearnerOnPC", f"Something went wrong:\n\n{exc}")
    except Exception:
        pass


def selftest() -> int:
    """Imports every module and checks the bundled demo data. No window, no AI model."""
    import tempfile
    import config
    result = config.data_dir() / "selftest_result.txt"
    result.write_text("RUNNING\n", encoding="utf-8")
    for m in ("config", "store", "master", "search", "ai_engine", "app_window"):
        __import__(m)
    import master, search
    from store import Session
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
    try:
        assert config.install_demo_sessions() >= 1, "no demo sessions bundled"
        rows = Session.list_sessions()
        for r in rows:
            s = Session.load(r["id"])
            assert s.blurred, f"{s.title}: transcript not marked blurred"
            assert all(not x.get("text") for x in s.screens), "screen text must be empty"
            search.build(s)
        assert len(master.payload()["nodes"]) > 0
        assert isinstance(search.search("civilization"), list)
        for name in ("master3d.html", "library.html", "graph3d.html", "mindmap.html", "studypack.html"):
            assert (config.resource_path("assets") / name).exists(), name
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old
    print("SELFTEST OK", config.APP_VERSION, f"({len(rows)} demo sessions)")
    result.write_text(f"SELFTEST OK {config.APP_VERSION}\n", encoding="utf-8")
    return 0


def main() -> int:
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-logging")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    import ai_engine
    import config
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setWindowIcon(QIcon(str(config.resource_path("assets") / "icon.png")))
    sys.excepthook = _crash_handler
    config.install_demo_sessions()
    ai_engine.stop_leftovers()
    from app_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _prepare_streams()
    if "--selftest" in sys.argv:
        try:
            sys.exit(selftest())
        except Exception:
            traceback.print_exc()
            sys.exit(1)
    sys.exit(main())
