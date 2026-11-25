"""Settings and paths for LearnerOnPC."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "LearnerOnPC"
APP_VERSION = "0.9.0"

DEMO_NOTICE = (
    "LearnerOnPC is a demo with limited features. It shows sessions that were already made, and answers questions "
    "about them. It cannot record or analyse anything new.")


def resource_path(*parts: str) -> Path:
    """Path to a bundled read-only file, both when running from source and when packaged by PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return data_dir() / "learneronpc.log"


def sessions_dir() -> Path:
    p = data_dir() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def master_overrides_file() -> Path:
    return data_dir() / "master_overrides.json"


def install_demo_sessions() -> int:
    """Copy the bundled demo sessions into the data folder (first start, or when one is missing).
    Returns how many were copied."""
    src = resource_path("demo_sessions")
    n = 0
    if not src.exists():
        return 0
    for d in sorted(src.iterdir()):
        if d.is_dir() and not (sessions_dir() / d.name / "session.json").exists():
            shutil.copytree(d, sessions_dir() / d.name, dirs_exist_ok=True)
            n += 1
    return n


@dataclass
class Settings:
    model_ready_seen: bool = False      # the model-download dialog was shown once
    ask_threads: int = 0                # 0 = automatic

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            for k, v in raw.items():
                if k in known:
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        (data_dir() / "settings.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
