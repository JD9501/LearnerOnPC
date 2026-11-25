"""Session data (read-only demo): notes, knowledge graph, mind map, study pack. Transcript and screen text of the
demo sessions are blurred."""
from __future__ import annotations

import difflib
import json
import re
import secrets
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from config import sessions_dir

# One label for anything that comes from the model's general knowledge rather than the user's sessions
OUTSIDE_LABEL = "[Outside the video - general knowledge, not checked against your sessions]"


def is_outside(text) -> bool:
    return isinstance(text, str) and text.strip().startswith(OUTSIDE_LABEL)


def unmark_outside(text: str) -> str:
    t = (text or "").strip()
    return t[len(OUTSIDE_LABEL):].strip() if t.startswith(OUTSIDE_LABEL) else t


def mark_outside(text: str) -> str:
    t = unmark_outside(text)
    return f"{OUTSIDE_LABEL} {t}" if t else ""

STOPWORDS = set(
    "a an the and or but if then so of to in on at by for with about as is are was were be been being "
    "this that these those it its i you he she we they them his her our your their what which who whom "
    "how why when where do does did done have has had not no yes can could should would will just than "
    "from into out up down over under again more most some any each few very also like get got".split()
)


def fmt_time(t: float) -> str:
    t = int(max(0, t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def norm_key(name: str) -> str:
    s = re.sub(r"[^\w\s\-]", "", str(name).lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]


def label_propagation(nodes: list[str], edges: list[tuple[str, str, float]], iters: int = 12) -> dict[str, int]:
    """Community detection (greedy modularity, one Louvain level). The old label-propagation put ~75% of a
    dense graph into one cluster; modularity keeps clusters meaningful. Name kept so callers don't change."""
    adj: dict[str, dict[str, float]] = {n: defaultdict(float) for n in nodes}
    for a, b, w in edges:
        if a in adj and b in adj and a != b:
            adj[a][b] += w
            adj[b][a] += w
    deg = {n: sum(adj[n].values()) for n in nodes}
    m2 = sum(deg.values())
    order = sorted(nodes)
    comm = {n: i for i, n in enumerate(order)}
    if m2 > 0:
        sigma = {comm[n]: deg[n] for n in order}
        for _ in range(max(iters, 20)):
            moved = False
            for n in order:
                if not adj[n]:
                    continue
                ci = comm[n]
                links: dict[int, float] = defaultdict(float)
                for m, w in adj[n].items():
                    links[comm[m]] += w
                sigma[ci] -= deg[n]
                best, gain = ci, links.get(ci, 0.0) - sigma[ci] * deg[n] / m2
                for c, l in sorted(links.items()):
                    g = l - sigma[c] * deg[n] / m2
                    if g > gain + 1e-12:
                        best, gain = c, g
                sigma[best] = sigma.get(best, 0.0) + deg[n]
                if best != ci:
                    comm[n] = best
                    moved = True
            if not moved:
                break
        # fold tiny clusters (1-2 nodes) into the neighbouring cluster they are most tied to
        size: dict[int, int] = defaultdict(int)
        for c in comm.values():
            size[c] += 1
        for n in order:
            if size[comm[n]] <= 2 and adj[n]:
                tie: dict[int, float] = defaultdict(float)
                for m, w in adj[n].items():
                    if comm[m] != comm[n] and size[comm[m]] > 2:
                        tie[comm[m]] += w
                if tie:
                    size[comm[n]] -= 1
                    comm[n] = sorted(tie.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                    size[comm[n]] += 1
    remap = {old: i for i, old in enumerate(sorted(set(comm.values()), key=lambda c: (-sum(1 for v in comm.values() if v == c), c)))}
    return {n: remap[c] for n, c in comm.items()}


TYPES = ("concept", "term", "person", "place", "event", "era", "material", "tool", "example", "formula")


def norm_type(t) -> str:
    t = str(t or "").strip().lower()
    return t if t in TYPES else "concept"


class Session:
    def __init__(self, title: str = "Untitled session", sid: str | None = None) -> None:
        self.id = sid or (time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2))
        self.title = title
        self.title_is_auto = True
        self.created = time.time()
        self.segments: list[dict] = []
        self.screens: list[dict] = []
        self.notes: list[dict] = []
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.mindmap: dict | None = None
        self.pack: dict = {}
        self.pack_meta: dict = {}      # {"notes": n, "t": seconds covered, "at": time}
        self.audit: list[dict] = []    # things the fact-checker removed or corrected
        self.topic = ""
        self.mode = "recorded"          # recorded | meeting
        self.source = "app"             # app | extension
        self.url = ""
        self.meeting: dict = {"decisions": [], "actions": []}
        self.links: dict = {"nodes": {}, "edges": {}, "picked": []}   # meeting parts linked into the master graph
        self.analysis: dict = {"state": "legacy", "items": {}}
        self.blurred = False            # transcript and screens blurred (made from someone else's video)
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ paths
    @property
    def dir(self) -> Path:
        p = sessions_dir() / self.id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def pack_status(self) -> dict:
        """Is the study pack older than the notes? Used for the 'stale' banner."""
        with self.lock:
            has = bool(self.pack.get("summary") or self.pack.get("tldr"))
            m = self.pack_meta or {}
            n_notes = len(self.notes)
            dur = self.duration()
        if not has:
            return {"exists": False, "stale": n_notes > 0, "covered": 0, "now": dur, "missing": n_notes}
        covered = float(m.get("t", 0.0))
        missing = max(0, n_notes - int(m.get("notes", n_notes)))
        return {"exists": True, "stale": missing > 0 or dur - covered > 60, "covered": covered, "now": dur, "missing": missing}

    def web_data(self) -> dict:
        """A 3D 'web' of the mind map (used in meeting mode, where no knowledge graph is built)."""
        tree = self.mindmap or {}
        nodes: list[dict] = []
        edges: list[dict] = []
        if tree.get("name"):
            def walk(n, path, parent, group, depth):
                nid = path
                kids = n.get("children") or []
                nodes.append({"id": nid, "label": str(n.get("name", ""))[:60], "type": "topic" if depth < 2 else "point",
                              "definition": "", "count": 1, "first_seen": 0.0, "last_seen": 0.0,
                              "degree": len(kids) + (1 if parent else 0), "group": group})
                if parent:
                    edges.append({"source": parent, "target": nid, "label": "", "weight": 1})
                for i, c in enumerate(kids):
                    walk(c, f"{path}/{i}", nid, (i if depth == 0 else group), depth + 1)
            walk(tree, "0", None, 0, 0)
        return {"nodes": nodes, "edges": edges, "duration": 0.0}

    # ----------------------------------------------------------------- queries
    @property
    def integrated(self) -> bool:
        return self.analysis.get("state") in ("integrated", "legacy")

    def duration(self) -> float:
        with self.lock:
            return self.segments[-1]["end"] if self.segments else 0.0

    def word_count(self) -> int:
        with self.lock:
            return sum(len(s["text"].split()) for s in self.segments)

    def graph_data(self, max_nodes: int = 150) -> dict:
        with self.lock:
            nodes = {k: dict(v) for k, v in self.nodes.items()}
            edges = [dict(e) for e in self.edges.values()]
        deg: Counter = Counter()
        for e in edges:
            deg[e["source"]] += 1
            deg[e["target"]] += 1
        for k, n in nodes.items():
            n["degree"] = deg.get(k, 0)
        if len(nodes) > max_nodes:
            keep = sorted(nodes, key=lambda k: nodes[k]["count"] + 2 * nodes[k]["degree"], reverse=True)[:max_nodes]
            keep_set = set(keep)
            nodes = {k: nodes[k] for k in keep}
            edges = [e for e in edges if e["source"] in keep_set and e["target"] in keep_set]
        groups = label_propagation(list(nodes), [(e["source"], e["target"], e["weight"]) for e in edges])
        for k, n in nodes.items():
            n["group"] = groups.get(k, 0)
        return {"nodes": list(nodes.values()), "edges": edges, "duration": self.duration()}

    def analytics(self) -> dict:
        g = self.graph_data()
        nodes = g["nodes"]
        by_id = {n["id"]: n for n in nodes}
        core = sorted(nodes, key=lambda n: (2 * n["degree"] + n["count"]), reverse=True)[:10]
        path = sorted(nodes, key=lambda n: n["first_seen"])[:25]
        comm: dict[int, list[dict]] = defaultdict(list)
        for n in nodes:
            comm[n["group"]].append(n)
        communities = []
        for members in comm.values():
            members.sort(key=lambda n: (n["degree"], n["count"]), reverse=True)
            communities.append({"name": members[0]["label"], "size": len(members),
                                "members": [m["label"] for m in members[:12]]})
        communities.sort(key=lambda c: c["size"], reverse=True)
        dur = max(self.duration(), 1.0)
        words = self.word_count()
        types = Counter(n["type"] for n in nodes)
        return {
            "stats": {
                "duration": fmt_time(self.duration()), "words": words,
                "wpm": round(words / (dur / 60.0)) if words else 0,
                "concepts": len(nodes), "relations": len(g["edges"]),
                "notes": sum(len(n["points"]) for n in self.notes),
                "screens": len(self.screens),
            },
            "core": [{"label": n["label"], "score": 2 * n["degree"] + n["count"], "degree": n["degree"],
                      "mentions": n["count"]} for n in core],
            "path": [{"label": n["label"], "t": fmt_time(n["first_seen"]), "seconds": n["first_seen"]} for n in path],
            "communities": communities[:8],
            "types": dict(types),
            "isolated": [by_id[k]["label"] for k in by_id if by_id[k]["degree"] == 0][:15],
        }

    def chapters(self) -> list[dict]:
        out: list[dict] = []
        with self.lock:
            notes = list(self.notes)
        for n in notes:
            if out and norm_key(out[-1]["title"]) == norm_key(n["title"]):
                continue
            out.append({"t": n["t"], "time": fmt_time(n["t"]), "title": n["title"]})
        return out

    # ------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "title": self.title, "title_is_auto": self.title_is_auto,
                "created": self.created, "topic": self.topic, "segments": self.segments,
                "screens": self.screens, "notes": self.notes, "nodes": self.nodes,
                "edges": self.edges, "mindmap": self.mindmap, "pack": self.pack,
                "pack_meta": self.pack_meta, "audit": self.audit[-300:],
                "mode": self.mode, "source": self.source, "url": self.url,
                "meeting": self.meeting, "links": self.links, "analysis": self.analysis,
                "demo_blurred": self.blurred,
            }

    def summary_dict(self) -> dict:
        """Small per-session file the master graph and library read (so they never load whole transcripts)."""
        with self.lock:
            nodes = self.links["nodes"] if self.mode == "meeting" else self.nodes
            edges = self.links["edges"] if self.mode == "meeting" else self.edges
            return {
                "id": self.id, "title": self.title, "mode": self.mode, "source": self.source, "url": self.url,
                "created": self.created, "duration": self.duration(), "words": self.word_count(),
                "segments": len(self.segments), "notes": sum(len(n["points"]) for n in self.notes),
                "topic": self.topic, "has_pack": bool(self.pack.get("summary") or self.pack.get("tldr")),
                "linked": bool(self.links.get("picked")), "decisions": len(self.meeting["decisions"]),
                "actions": len(self.meeting["actions"]), "nodes": nodes, "edges": edges,
                "analysis": self.analysis.get("state", "legacy"),
            }

    def write_summary(self) -> None:
        path = self.dir / "summary.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.summary_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def save(self) -> None:
        path = self.dir / "session.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        try:
            self.write_summary()
        except OSError:
            pass

    @classmethod
    def load(cls, sid: str) -> "Session":
        raw = json.loads((sessions_dir() / sid / "session.json").read_text(encoding="utf-8"))
        s = cls(raw.get("title", "Untitled"), sid=raw.get("id", sid))
        s.title_is_auto = raw.get("title_is_auto", False)
        s.created = raw.get("created", time.time())
        s.topic = raw.get("topic", "")
        for key in ("segments", "screens", "notes", "nodes", "edges", "pack"):
            if key in raw:
                setattr(s, key, raw[key])
        s.mindmap = raw.get("mindmap")
        s.pack_meta = raw.get("pack_meta") if isinstance(raw.get("pack_meta"), dict) else {}
        s.audit = list(raw.get("audit", []))
        for n in s.nodes.values():          # old sessions: tidy the node types into the fixed set
            n["type"] = norm_type(n.get("type"))
        # older sessions simply get the defaults
        s.mode = raw.get("mode", "recorded") if raw.get("mode") in ("recorded", "meeting") else "recorded"
        s.source = raw.get("source", "app")
        s.url = raw.get("url", "")
        m = raw.get("meeting") if isinstance(raw.get("meeting"), dict) else {}
        s.meeting = {"decisions": list(m.get("decisions", [])), "actions": list(m.get("actions", []))}
        lk = raw.get("links") if isinstance(raw.get("links"), dict) else {}
        s.links = {"nodes": dict(lk.get("nodes", {})), "edges": dict(lk.get("edges", {})), "picked": list(lk.get("picked", []))}
        an = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else None
        s.analysis = an if an else {"state": "legacy", "items": {}}
        s.blurred = bool(raw.get("demo_blurred"))
        return s

    @staticmethod
    def list_sessions() -> list[dict]:
        out = []
        for d in sorted(sessions_dir().iterdir(), reverse=True):
            f = d / "session.json"
            if not f.exists():
                continue
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": raw.get("id", d.name), "title": raw.get("title", d.name),
                            "created": raw.get("created", 0), "segments": len(raw.get("segments", [])),
                            "mode": raw.get("mode", "recorded"), "source": raw.get("source", "app")})
            except (OSError, ValueError):
                continue
        return out

    # ------------------------------------------------------------------ export
    def export_markdown(self) -> str:
        p = self.pack or {}
        L = [f"# {self.title}", "", f"*Exported from LearnerOnPC - {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.created))}*", ""]
        if p.get("tldr"):
            L += ["## TL;DR", p["tldr"], ""]
        if p.get("summary"):
            L += ["## Summary", p["summary"], ""]
        if p.get("takeaways"):
            L += ["## Key takeaways"] + [f"- {t}" for t in p["takeaways"]] + [""]
        if self.notes:
            L += ["## Notes"]
            for n in self.notes:
                L.append(f"### [{fmt_time(n['t'])}] {n['title']}")
                L += [f"- {pt}" for pt in n["points"]]
                L.append("")
        if self.meeting["decisions"]:
            L += ["## Decisions"] + [f"- [{fmt_time(d['t'])}] {d['text']}" for d in self.meeting["decisions"]] + [""]
        if self.meeting["actions"]:
            L += ["## Action items"]
            for a in self.meeting["actions"]:
                L.append(f"- {a['task']}" + (f" - owner: {a['owner']}" if a["owner"] else "") + (f" - due: {a['due']}" if a["due"] else ""))
            L.append("")
        if self.nodes:
            L += ["## Concepts"]
            for n in sorted(self.nodes.values(), key=lambda x: -x["count"]):
                L.append(f"- **{n['label']}** ({n['type']}): {n['definition'] or '-'}")
            L.append("")
        if self.edges:
            L += ["## Relationships"]
            for e in self.edges.values():
                a = self.nodes.get(e["source"], {}).get("label", e["source"])
                b = self.nodes.get(e["target"], {}).get("label", e["target"])
                L.append(f"- {a} -- {e['label']} --> {b}")
            L.append("")
        if p.get("flashcards"):
            L += ["## Flashcards"] + [f"- **Q:** {c['q']}  \n  **A:** {c['a']}" for c in p["flashcards"]] + [""]
        if p.get("quiz"):
            L += ["## Quiz"]
            for i, q in enumerate(p["quiz"], 1):
                L.append(f"{i}. {q['q']}")
                for j, o in enumerate(q["options"]):
                    L.append(f"   - {'ABCDEF'[j]}. {o}")
                L.append(f"   - Answer: {'ABCDEF'[q['answer']]} - {q.get('explain', '')}")
            L.append("")
        return "\n".join(L)
