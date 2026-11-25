"""Word search for Ask. One SQLite full-text index per session (index.sqlite in the session folder), built from the
session's own study material: note points, concept definitions and the study-pack summary. The transcripts of the
demo sessions are blurred, and note points that repeat a video word for word are hidden, so neither is searched."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from config import sessions_dir
from store import Session, fmt_time

STOP = {"what", "does", "the", "and", "how", "why", "who", "which", "with", "about", "this", "that", "from", "are", "was",
        "were", "did", "can", "you", "tell", "explain", "video", "session", "there"}


def chunks_of(s: Session) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for n in s.notes:
        for p in n.get("points", []):
            if not str(p).startswith("[Hidden"):          # word for word from the video: not shown, not searched
                out.append(("note", float(n.get("t", 0.0)), f"{n.get('title', '')}: {p}"))
    for n in s.nodes.values():
        if n.get("definition"):
            out.append(("concept", float(n.get("first_seen", 0.0)), f"{n['label']}: {n['definition']}"))
    if s.pack.get("summary") and not str(s.pack["summary"]).startswith("[Hidden"):
        out.append(("summary", 0.0, str(s.pack["summary"])))
    return out


def _db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, kind TEXT, t REAL, text TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='chunks', content_rowid='id')")
    return con


def build(s: Session) -> int:
    path = s.dir / "index.sqlite"
    if path.exists():
        path.unlink()
    con = _db(path)
    rows = chunks_of(s)
    con.executemany("INSERT INTO chunks(kind, t, text) VALUES (?,?,?)", rows)
    con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
    con.commit()
    con.close()
    return len(rows)


def ensure_all() -> None:
    for m in Session.list_sessions():
        if not (sessions_dir() / m["id"] / "index.sqlite").exists():
            try:
                build(Session.load(m["id"]))
            except Exception:
                pass


def words(q: str) -> str:
    w = [x for x in re.findall(r"[A-Za-z0-9]{3,}", q.lower()) if x not in STOP]
    return " OR ".join(f'"{x}"' for x in w[:12])


def search(question: str, k: int = 12) -> list[dict]:
    q = words(question)
    if not q:
        return []
    hits = []
    for m in Session.list_sessions():
        path = sessions_dir() / m["id"] / "index.sqlite"
        if not path.exists():
            continue
        con = _db(path)
        try:
            rows = con.execute("SELECT c.kind, c.t, c.text, bm25(fts) FROM fts JOIN chunks c ON c.id = fts.rowid "
                               "WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 20", (q,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        con.close()
        for kind, t, text, score in rows:
            hits.append({"session": m["id"], "title": m["title"], "kind": kind, "t": t, "time": fmt_time(t),
                         "text": text, "score": score})
    return sorted(hits, key=lambda h: h["score"])[:k]
