"""Master knowledge graph: one 3D space built from every session's concepts.

Rule: connections are made only from evidence. Two sessions join up when they contain the *same* concept
(same canonical name). Near-matches are never merged automatically - they become suggestions a person can accept
or dismiss. Sessions that share nothing stay as separate islands (separate webs in the 3D space).
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
from collections import Counter, defaultdict

from config import master_overrides_file, sessions_dir
from store import fmt_time, label_propagation, norm_key

FUZZY_ACCEPT = 0.93      # automatic merge threshold (with the digit guard below)
SUGGEST_MIN = 0.80       # below the accept bar but close enough to ask a person
_LOCK = threading.RLock()
_CACHE: dict = {"sig": None, "built": None}


# ------------------------------------------------------------------ keys
def canon_key(name: str) -> str:
    """Canonical form used for exact merging: lowercase, no punctuation, no leading article, light plural stem."""
    k = norm_key(name).replace("-", " ")
    k = re.sub(r"^(the|a|an)\s+", "", k)
    words = []
    for w in k.split():
        if len(w) > 3 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def _digits(k: str) -> tuple:
    return tuple(re.findall(r"\d+", k))


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.p[rb] = ra


# ------------------------------------------------------------------ overrides
def load_overrides() -> dict:
    try:
        raw = json.loads(master_overrides_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {"merge": [list(p) for p in raw.get("merge", []) if isinstance(p, list) and len(p) == 2],
            "dismissed": [list(p) for p in raw.get("dismissed", []) if isinstance(p, list) and len(p) == 2],
            "excluded": [s for s in raw.get("excluded", []) if isinstance(s, str)]}


def save_overrides(o: dict) -> None:
    p = master_overrides_file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    invalidate()


def accept_suggestion(a: str, b: str) -> None:
    o = load_overrides()
    pair = sorted([a, b])
    if pair not in o["merge"]:
        o["merge"].append(pair)
    save_overrides(o)


def dismiss_suggestion(a: str, b: str) -> None:
    o = load_overrides()
    pair = sorted([a, b])
    if pair not in o["dismissed"]:
        o["dismissed"].append(pair)
    save_overrides(o)


def set_excluded(session_id: str, excluded: bool) -> None:
    o = load_overrides()
    cur = set(o["excluded"])
    (cur.add if excluded else cur.discard)(session_id)
    o["excluded"] = sorted(cur)
    save_overrides(o)


def invalidate() -> None:
    with _LOCK:
        _CACHE["sig"] = None
        _CACHE["all_sig"] = None          # the all-sessions cache was missed; two saves within one Windows
                                          # file-time tick then showed the old graph (seen in the updater self-test)


# ------------------------------------------------------------------ loading
def _read_summary(d) -> dict | None:
    sm = d / "summary.json"
    try:
        if sm.exists():
            return json.loads(sm.read_text(encoding="utf-8"))
        sj = d / "session.json"
        if sj.exists():       # session saved by build the summary on the fly (nothing is rewritten)
            from store import Session
            s = Session.load(d.name)
            data = s.summary_dict()
            try:
                s.write_summary()
            except OSError:
                pass
            return data
    except (OSError, ValueError):
        return None
    return None


def _signature() -> tuple:
    sig = []
    try:
        for d in sorted(sessions_dir().iterdir()):
            for fn in ("summary.json", "session.json"):
                f = d / fn
                if f.exists():
                    sig.append((d.name, fn, f.stat().st_mtime_ns))
                    break
        f = master_overrides_file()
        sig.append(("ov", f.stat().st_mtime_ns if f.exists() else 0))
    except OSError:
        pass
    return tuple(sig)


def load_summaries() -> list[dict]:
    out = []
    try:
        dirs = sorted(sessions_dir().iterdir())
    except OSError:
        return out
    for d in dirs:
        if d.is_dir():
            sm = _read_summary(d)
            if sm and sm.get("id"):
                out.append(sm)
    return out


# ------------------------------------------------------------------ build
def build() -> dict:
    """Return the merged graph + analytics. Cached until any session file changes."""
    with _LOCK:
        sig = _signature()
        if _CACHE["sig"] == sig and _CACHE["built"] is not None:
            return _CACHE["built"]
        built = _build(load_summaries(), load_overrides())
        _CACHE["sig"], _CACHE["built"] = sig, built
        return built


def _semantic_pairs() -> list[tuple[str, str, float]]:
    """'Same thing?' pairs saved by meaning search: [(name a, name b, similarity)]. None in this demo."""
    try:
        from config import data_dir
        raw = json.loads((data_dir() / "semantic_pairs.json").read_text(encoding="utf-8"))
        return [(a, b, float(r)) for a, b, r in raw]
    except (OSError, ValueError, TypeError):
        return []


def _build(sums: list[dict], ov: dict) -> dict:
    excluded = set(ov["excluded"])
    # a session joins the shared views only once its analysis is finished
    sums = [s for s in sums if s["id"] not in excluded and s.get("analysis", "legacy") in ("integrated", "legacy")]
    meta = {s["id"]: s for s in sums}

    # 1. collect every (session, concept) under its canonical key
    ent: dict[str, dict] = {}
    for s in sums:
        for k, n in (s.get("nodes") or {}).items():
            label = n.get("label") or k
            ck = canon_key(label)
            if len(ck) < 2:
                continue
            e = ent.setdefault(ck, {"labels": Counter(), "types": Counter(), "defs": [], "sessions": {}, "first": None})
            e["labels"][label] += n.get("count", 1) or 1
            e["types"][n.get("type", "concept")] += 1
            if n.get("definition"):
                e["defs"].append(n["definition"])
            e["sessions"][s["id"]] = e["sessions"].get(s["id"], 0) + (n.get("count", 1) or 1)
            fs = float(n.get("first_seen", 0.0))
            e["first"] = fs if e["first"] is None else min(e["first"], fs)

    # 2. automatic fuzzy merge (strict) + person-accepted merges
    uf = _UF()
    keys = sorted(ent)
    for k in keys:
        uf.find(k)
    dismissed = {tuple(sorted(p)) for p in ov["dismissed"]}
    for a, b in ov["merge"]:
        ca, cb = canon_key(a), canon_key(b)
        if ca in ent and cb in ent:
            uf.union(ca, cb)
    suggestions: list[dict] = []
    by_first: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        by_first[k[:1]].append(k)
    for bucket in by_first.values():                     # only compare words that start alike: cheap and sensible
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if len(a) < 5 or len(b) < 5 or uf.find(a) == uf.find(b):
                    continue
                ta, tb = set(a.split()), set(b.split())
                subset = (ta < tb or tb < ta) and abs(len(ta) - len(tb)) <= 2 and len(ta & tb) >= 1
                if abs(len(a) - len(b)) > 6 and not subset:
                    continue
                r = max(difflib.SequenceMatcher(None, a, b).ratio(), SUGGEST_MIN if subset else 0.0)
                if r < SUGGEST_MIN:
                    continue
                if _digits(a) != _digits(b):             # "gpt 3" vs "gpt 4": never the same thing
                    continue
                if r >= FUZZY_ACCEPT:
                    uf.union(a, b)
                elif tuple(sorted([a, b])) not in dismissed and set(ent[a]["sessions"]) != set(ent[b]["sessions"]):
                    suggestions.append({"a": a, "b": b, "ratio": round(r, 3)})

    seen = {tuple(sorted([x["a"], x["b"]])) for x in suggestions}
    for a, b, r in _semantic_pairs():
        ca, cb = canon_key(a), canon_key(b)
        pair = tuple(sorted([ca, cb]))
        if (ca in ent and cb in ent and uf.find(ca) != uf.find(cb) and pair not in dismissed and pair not in seen
                and _digits(ca) == _digits(cb)):
            suggestions.append({"a": ca, "b": cb, "ratio": round(min(0.92, r), 3)})
            seen.add(pair)

    # 3. master nodes
    groups: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        groups[uf.find(k)].append(k)
    node_of: dict[str, str] = {}
    mnodes: dict[str, dict] = {}
    for root, members in groups.items():
        labels, types, defs, sess = Counter(), Counter(), [], {}
        first = None
        for m in members:
            e = ent[m]
            labels.update(e["labels"])
            types.update(e["types"])
            defs += e["defs"]
            for sid, c in e["sessions"].items():
                sess[sid] = sess.get(sid, 0) + c
            first = e["first"] if first is None else min(first, e["first"])
            node_of[m] = root
        mnodes[root] = {
            "id": root, "label": labels.most_common(1)[0][0], "type": types.most_common(1)[0][0],
            "definition": max(defs, key=len) if defs else "", "count": sum(sess.values()),
            "sessions": sorted(sess), "n_sessions": len(sess), "first_seen": first or 0.0, "last_seen": first or 0.0,
        }

    # 4. edges: only those some session actually stated
    medges: dict[tuple, dict] = {}
    for s in sums:
        for e in (s.get("edges") or {}).values():
            sa = (s.get("nodes") or {}).get(e["source"])
            sb = (s.get("nodes") or {}).get(e["target"])
            if not sa or not sb:
                continue
            a = node_of.get(canon_key(sa.get("label", "")))
            b = node_of.get(canon_key(sb.get("label", "")))
            if not a or not b or a == b:
                continue
            key = (a, b, norm_key(e.get("label", "")))
            m = medges.setdefault(key, {"source": a, "target": b, "label": e.get("label", "related to"),
                                        "weight": 0, "sessions": set()})
            m["weight"] += e.get("weight", 1)
            m["sessions"].add(s["id"])
    edge_list = [dict(v, sessions=sorted(v["sessions"])) for v in medges.values()]

    # 5. islands (connected components) and communities inside each island
    iu = _UF()
    for k in mnodes:
        iu.find(k)
    for e in edge_list:
        iu.union(e["source"], e["target"])
    comps: dict[str, list[str]] = defaultdict(list)
    for k in mnodes:
        comps[iu.find(k)].append(k)
    ordered = sorted(comps.values(), key=lambda c: (-len(c), sorted(c)[0]))
    island_of: dict[str, int] = {}
    for i, comp in enumerate(ordered):
        for k in comp:
            island_of[k] = i
    comm = label_propagation(list(mnodes), [(e["source"], e["target"], e["weight"]) for e in edge_list])
    deg: Counter = Counter()
    for e in edge_list:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    for k, n in mnodes.items():
        n["degree"] = deg.get(k, 0)
        n["island"] = island_of[k]
        n["group"] = comm.get(k, 0)

    islands = []
    for i, comp in enumerate(ordered):
        sids = sorted({sid for k in comp for sid in mnodes[k]["sessions"]})
        top = sorted(comp, key=lambda k: (-mnodes[k]["degree"], -mnodes[k]["count"]))[:6]
        islands.append({"index": i, "size": len(comp), "sessions": sids,
                        "label": mnodes[top[0]]["label"], "top": [mnodes[k]["label"] for k in top]})

    # sessions with no concepts at all are still listed (they simply have nothing to draw yet)
    sess_rows = []
    for s in sums:
        sess_rows.append({k: s.get(k) for k in ("id", "title", "mode", "source", "url", "created", "duration", "words",
                                                "segments", "notes", "topic", "has_pack", "linked", "decisions", "actions")}
                         | {"concepts": len(s.get("nodes") or {})})
    sess_rows.sort(key=lambda r: r.get("created") or 0, reverse=True)

    sug = []
    for x in sorted(suggestions, key=lambda x: -x["ratio"])[:40]:
        if x["a"] in node_of and x["b"] in node_of:
            sug.append({"a": x["a"], "b": x["b"], "ratio": x["ratio"],
                        "a_label": mnodes[node_of[x["a"]]]["label"], "b_label": mnodes[node_of[x["b"]]]["label"],
                        "a_sessions": [meta[i]["title"] for i in ent[x["a"]]["sessions"] if i in meta][:3],
                        "b_sessions": [meta[i]["title"] for i in ent[x["b"]]["sessions"] if i in meta][:3]})
    return {"nodes": mnodes, "edges": edge_list, "islands": islands, "sessions": sess_rows,
            "suggestions": sug, "built_at": time.time()}


# ------------------------------------------------------------------ outputs
def all_sessions() -> list[dict]:
    """Every saved session, for the checklist. Sessions hidden with the old 'exclude' flag start unticked."""
    ex = set(load_overrides()["excluded"])
    out = []
    for sm in load_summaries():
        out.append({"id": sm["id"], "title": sm.get("title") or sm["id"], "mode": sm.get("mode", "recorded"),
                    "hidden": sm["id"] in ex, "concepts": len(sm.get("nodes") or {})})
    return out


def default_selection() -> list[str]:
    return [x["id"] for x in all_sessions() if not x["hidden"]]


def _build_all() -> dict:
    """Graph of every session (including ones hidden by the old exclude flag), cached until a file changes."""
    with _LOCK:
        sig = _signature()
        if _CACHE.get("all_sig") == sig and _CACHE.get("all") is not None:
            return _CACHE["all"]
        built = _build(load_summaries(), {**load_overrides(), "excluded": []})
        _CACHE["all_sig"], _CACHE["all"] = sig, built
        return built


def payload(limit: int | None = None, session_id: str | None = None, island: int | None = None,
            selected: list[str] | None = None) -> dict:
    """Data for the 3D page: the graph of ALL sessions. Every node and edge lists the sessions it comes from, so the
    page can show or hide any subset instantly. `selected` = which sessions start ticked (None = all not hidden)."""
    b = _build_all()
    nodes = list(b["nodes"].values())
    if session_id:
        nodes = [n for n in nodes if session_id in n["sessions"]]
    if island is not None:
        nodes = [n for n in nodes if n["island"] == island]
    if limit and len(nodes) > limit:
        nodes = sorted(nodes, key=lambda n: n["count"] + 2 * n["degree"] + 3 * n["n_sessions"], reverse=True)[:limit]
    keep = {n["id"] for n in nodes}
    edges = [e for e in b["edges"] if e["source"] in keep and e["target"] in keep]
    names = {s["id"]: s["title"] for s in b["sessions"]}
    out_nodes = []
    for n in nodes:
        d = dict(n)
        d["session_titles"] = [names.get(i, i) for i in n["sessions"]][:6]
        out_nodes.append(d)
    return {"nodes": out_nodes, "edges": edges, "duration": 0.0,
            "islands": b["islands"], "sessions": b["sessions"], "suggestions": b["suggestions"],
            "all_sessions": all_sessions(),
            "selected": list(selected) if selected is not None else default_selection()}


def analytics_payload() -> dict:
    b = build()
    nodes = list(b["nodes"].values())
    rows = b["sessions"]
    shared = sorted((n for n in nodes if n["n_sessions"] > 1), key=lambda n: (-n["n_sessions"], -n["count"]))
    core = sorted(nodes, key=lambda n: -(2 * n["degree"] + n["count"]))[:12]
    types = Counter(n["type"] for n in nodes)
    names = {s["id"]: s["title"] for s in rows}
    pairs: Counter = Counter()          # which sessions are connected through shared concepts
    for n in nodes:
        ss = n["sessions"]
        for i in range(len(ss)):
            for j in range(i + 1, len(ss)):
                pairs[(ss[i], ss[j])] += 1
    return {
        "stats": {
            "sessions": len(rows), "meetings": sum(1 for r in rows if r["mode"] == "meeting"),
            "recorded": sum(1 for r in rows if r["mode"] != "meeting"),
            "from_extension": sum(1 for r in rows if r.get("source") == "extension"),
            "concepts": len(nodes), "relations": len(b["edges"]), "islands": len(b["islands"]),
            "bridging": len(shared), "hours": round(sum((r.get("duration") or 0) for r in rows) / 3600.0, 1),
            "words": sum((r.get("words") or 0) for r in rows),
        },
        "core": [{"label": n["label"], "score": 2 * n["degree"] + n["count"], "sessions": n["n_sessions"]} for n in core],
        "bridges": [{"label": n["label"], "sessions": [names.get(i, i) for i in n["sessions"]][:5],
                     "n": n["n_sessions"]} for n in shared[:15]],
        "links": [{"a": names.get(a, a), "b": names.get(b2, b2), "shared": c}
                  for (a, b2), c in pairs.most_common(12)],
        "islands": b["islands"][:12],
        "types": dict(types),
        "suggestions": len(b["suggestions"]),
        "sessions": rows,
    }


def session_rows() -> list[dict]:
    return build()["sessions"]
