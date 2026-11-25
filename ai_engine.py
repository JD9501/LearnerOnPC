"""The one AI model in LearnerOnPC: it answers questions on the Ask tab, from the demo sessions' study material.

The model (Qwen3 4B Instruct 2507, Apache 2.0, about 2.5 GB) and the llama.cpp server program (MIT, about 25 MB) are
downloaded once into %LOCALAPPDATA%\\LearnerOnPC and run on this PC only. The server is started when a question is
asked and stopped when the Ask tab is left, when Stop is pressed, or when the app closes.
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from config import data_dir

MODEL_REPOS = ["lmstudio-community/Qwen3-4B-Instruct-2507-GGUF", "unsloth/Qwen3-4B-Instruct-2507-GGUF",
               "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF"]
MODEL_QUANT = "q4_k_m"
MODEL_LABEL = "Qwen3 4B Instruct 2507 (about 2.5 GB)"
MIN_RAM_GB = 8
SERVER_TAG = "b12359"
SERVER_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{SERVER_TAG}/llama-{SERVER_TAG}-bin-win-cpu-x64.zip"
CONTEXT = 8192
EXCERPT_BUDGET = 9000

ASK_SYSTEM = (
    "You answer a learner's question using only the study notes given to you. The notes come from videos the learner "
    "watched. Answer in plain, literal language and keep it short. After each sentence, give the session title and time "
    "it rests on, like [Title 12:30]. If the notes do not answer the question, say so in one sentence; do not add facts "
    "of your own.")
CHECK_SYSTEM = (
    "You check an answer against study notes. For each numbered sentence, say whether the notes support it. Reply with "
    "JSON only: {\"supported\": [true or false for each sentence, in order]}.")

Progress = Callable[[float, str], None]


class AIError(RuntimeError):
    pass


# ------------------------------------------------------------------ files
def model_dir() -> Path:
    p = data_dir() / "model"
    p.mkdir(parents=True, exist_ok=True)
    return p


def runtime_dir() -> Path:
    p = data_dir() / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_path() -> Path | None:
    try:
        info = json.loads((model_dir() / "model.json").read_text(encoding="utf-8"))
        p = model_dir() / info["file"]
        return p if p.exists() else None
    except (OSError, ValueError, KeyError):
        return None


def server_exe() -> Path | None:
    for p in runtime_dir().rglob("llama-server.exe" if sys.platform == "win32" else "llama-server"):
        return p
    return None


def ready() -> bool:
    return model_path() is not None and server_exe() is not None


def ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / 2**30
    except Exception:
        return 0.0


def _urlopen(url: str, timeout: float = 60):
    try:
        import truststore          # Windows' own certificate store (works behind company proxies)
        truststore.inject_into_ssl()
    except Exception:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": "LearnerOnPC"})
    return urllib.request.urlopen(req, timeout=timeout)


def download(progress: Progress) -> None:
    """Download the server program and the model (one time). Raises AIError."""
    if server_exe() is None:
        if sys.platform != "win32":
            raise AIError("The AI model runs on Windows only.")
        progress(0.0, "Downloading the AI server program (about 25 MB)...")
        try:
            with _urlopen(SERVER_URL, 600) as r:
                blob = r.read()
            zipfile.ZipFile(io.BytesIO(blob)).extractall(runtime_dir() / SERVER_TAG)
        except Exception as e:
            raise AIError(f"Could not download the AI server program: {e}") from e
        if server_exe() is None:
            raise AIError("The AI server download did not contain llama-server.exe.")
    if model_path() is None:
        try:
            from huggingface_hub import hf_hub_download, list_repo_files
        except ImportError as e:
            raise AIError("Missing package huggingface_hub.") from e
        repo, name, last = None, None, None
        for r in MODEL_REPOS:
            try:
                files = [f for f in list_repo_files(r) if f.lower().endswith(".gguf") and MODEL_QUANT in f.lower()
                         and "-of-" not in f.lower()]
            except Exception as e:
                last = e
                continue
            if files:
                repo, name = r, sorted(files)[0]
                break
        if repo is None:
            raise AIError(f"Could not reach the AI model on Hugging Face. Check the internet connection. ({last})")
        progress(0.05, f"Downloading the AI model {MODEL_LABEL}...")
        try:
            from tqdm.auto import tqdm

            class _Bar(tqdm):
                def update(self, n=1):
                    out = super().update(n)
                    if self.total:
                        progress(0.05 + 0.95 * min(1.0, self.n / self.total),
                                 f"Downloading the AI model... {int(100 * self.n / self.total)}%")
                    return out
            hf_hub_download(repo, name, local_dir=str(model_dir()), tqdm_class=_Bar)
        except TypeError:
            hf_hub_download(repo, name, local_dir=str(model_dir()))
        except Exception as e:
            raise AIError(f"The model download failed: {e}") from e
        (model_dir() / "model.json").write_text(json.dumps({"file": name, "repo": repo}), encoding="utf-8")
    progress(1.0, "The AI model is ready.")


# ------------------------------------------------------------------ server
_JOB = None


def _tie_to_this_process(proc: subprocess.Popen) -> None:
    """Windows: the server process ends when this app ends (a job object that kills its processes on close)."""
    global _JOB
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        if _JOB is None:
            class Basic(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                            ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

            class Ext(ctypes.Structure):
                _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6), ("p", ctypes.c_size_t),
                            ("q", ctypes.c_size_t), ("r", ctypes.c_size_t), ("s", ctypes.c_size_t)]
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            h = k32.CreateJobObjectW(None, None)
            info = Ext()
            info.Basic.LimitFlags = 0x2000                       # kill the processes when the job closes
            if h and k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                _JOB = (k32, h)
        if _JOB is not None:
            _JOB[0].AssignProcessToJobObject(_JOB[1], int(proc._handle))
    except Exception:
        pass


def stop_leftovers() -> int:
    """Stop llama-server processes from this app's folder that outlived an earlier run."""
    n = 0
    try:
        import psutil
        home = str(runtime_dir()).lower()
        me = os.getpid()
        for p in psutil.process_iter(["name", "exe", "ppid"]):
            try:
                if (p.info["name"] or "").lower().startswith("llama-server") and \
                        (p.info["exe"] or "").lower().startswith(home) and p.info["ppid"] != me:
                    p.kill()
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    return n


class Server:
    """One llama-server with one slot. start() waits until it is ready; stop() always ends it."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self._lock = threading.RLock()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, timeout: float = 180.0) -> None:
        with self._lock:
            if self.running():
                return
            exe, model = server_exe(), model_path()
            if exe is None or model is None:
                raise AIError("The AI model is not installed yet.")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
            threads = max(2, min(8, (os.cpu_count() or 4) - 2))
            cmd = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port), "-t", str(threads),
                   "-np", "1", "-c", str(CONTEXT), "--no-webui"]
            log = open(runtime_dir() / "server.log", "w", encoding="utf-8", errors="replace")
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=0x08000000 if sys.platform == "win32" else 0)
            _tie_to_this_process(self.proc)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if self.proc.poll() is not None:
                    raise AIError("The AI server stopped while starting (see runtime\\server.log).")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                        if json.loads(r.read().decode()).get("status") == "ok":
                            return
                except Exception:
                    pass
                time.sleep(0.5)
            self.stop()
            raise AIError("The AI server did not become ready in time.")

    def stop(self) -> None:
        with self._lock:
            p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except Exception:
                p.kill()

    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 900) -> str:
        if not self.running():
            raise AIError("stopped")
        body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2, "max_tokens": max_tokens, "seed": 7,
                "chat_template_kwargs": {"enable_thinking": False}}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                out = json.loads(r.read().decode("utf-8"))
            text = out["choices"][0]["message"]["content"] or ""
        except Exception as e:
            if not self.running():
                raise AIError("stopped") from e
            raise AIError(f"The AI server failed: {e}") from e
        return text.split("</think>", 1)[-1].strip()


# ------------------------------------------------------------------ Ask
def _sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])", text.strip()) if x.strip()]


def _json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1]) if i != -1 and j > i else {}


def ask(server: Server, question: str, hits: list[dict]) -> str:
    """Answer from the found excerpts, then keep only sentences the same model confirms against them."""
    if not hits:
        return "The demo sessions' notes do not cover that. Try asking about early human history or civilisations."
    parts: list[str] = []
    for h in hits:
        part = f"[{h['title'][:50]} {h['time']}] ({h['kind']}) {h['text']}"
        if parts and sum(len(p) + 1 for p in parts) + len(part) > EXCERPT_BUDGET:
            break
        parts.append(part)
    notes = "\n".join(parts)
    answer = server.chat(ASK_SYSTEM, f"STUDY NOTES:\n{notes}\n\nQUESTION: {question}")
    sents = _sentences(answer)
    if not sents:
        return "No answer came back. Please try again."
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sents))
    try:
        verdict = _json(server.chat(CHECK_SYSTEM, f"STUDY NOTES:\n{notes}\n\nSENTENCES:\n{numbered}", json_mode=True,
                                    max_tokens=300)).get("supported")
    except (AIError, ValueError):
        verdict = None
    if not isinstance(verdict, list) or len(verdict) != len(sents):
        return answer + "\n\n(This answer could not be double-checked against the notes.)"
    kept = [s for s, ok in zip(sents, verdict) if ok is True or str(ok).lower() == "true"]
    dropped = len(sents) - len(kept)
    if not kept:
        return "The answer could not be confirmed from the notes, so it is not shown."
    return " ".join(kept) + (f"\n\n({dropped} sentence(s) not found in the notes were left out.)" if dropped else "")
