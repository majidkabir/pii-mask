#!/usr/bin/env python3
"""pii-mask's local model server: GLiNER PII behind a small HTTP API.

The mod starts it when no session has (`--daemon`), and every session on the
machine shares it. It listens on 127.0.0.1 only, logs nothing it is sent, and
stops by itself after 30 minutes without requests.

Only the mod may use it: every request but /health carries the key in
<cache>/token (readable by this user alone), and a request a web page could
make (an Origin header, another site's Host) is refused. /health?nonce=N
answers sha256(key:N) as `proof`, so the mod knows the server on the port is
its own before it sends anything.

    <cache>/venv/bin/python server/server.py --cache ~/.cache/pii-mask [--daemon]
    (Windows: <cache>\\venv\\Scripts\\python.exe, cache %LOCALAPPDATA%\\pii-mask)

API (JSON):
    GET  /health[?nonce=N] -> {"app": "pii-mask", "ok": true, "model": ..., "proof": ...}
    POST /detect  {"texts": [...], "labels": ["person", "email", ...], "threshold": 0.5}
                  -> {"results": [[{"text", "label", "start", "end", "score"}, ...], ...]}
"""

import argparse
import base64
import hashlib
import hmac
import heapq
import itertools
import json
import os
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def plugin_version():
    """The mod's version this server came with, so a newer mod knows to replace it."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version", ""))
    except (OSError, ValueError):
        return ""


VERSION = plugin_version()


def peak_rss_mb():
    """The most memory this process has held, in MB (None where it can't be read)."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024))  # bytes on macOS, KB on Linux
    except ImportError:  # Windows
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            # Declared, or the 64-bit process handle is cut to 32 bits and the call reads nothing.
            current = ctypes.windll.kernel32.GetCurrentProcess
            current.restype = wintypes.HANDLE
            info = ctypes.windll.psapi.GetProcessMemoryInfo
            info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            info.restype = wintypes.BOOL
            c = Counters()
            c.cb = ctypes.sizeof(c)
            if not info(current(), ctypes.byref(c), c.cb):
                return None
            return round(c.PeakWorkingSetSize / (1024 * 1024))
        except Exception:
            return None

MODEL = "urchade/gliner_multi_pii-v1"
MODEL_REVISION = "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"  # the commit setup.py fetches
MAX_BODY = 16 * 1024 * 1024
BATCH = 4  # chunks per model call; between calls a smaller waiting request goes first
CACHE_SIZE = 20000  # chunks whose results are kept
CHUNK = 1000  # characters per model input; GLiNER reads about 384 words at a time
LINE_NUMBER = re.compile(r"[ \t]*\d+\t")


def pieces(text):
    """Splits text into chunks of whole lines up to CHUNK characters (a longer
    line is cut at a space), without the line numbers Claude Code's Read tool
    adds. Yields (offset, chunk)."""
    buf, start, offset = "", None, 0
    for line in text.splitlines(keepends=True):
        m = LINE_NUMBER.match(line)
        skip = m.end() if m else 0
        body = line[skip:]
        pos = offset + skip
        offset += len(line)
        while len(body) > CHUNK:
            cut = body.rfind(" ", 0, CHUNK)
            cut = cut if cut > CHUNK // 2 else CHUNK
            if buf:
                yield start, buf
                buf = ""
            yield pos, body[:cut]
            body, pos = body[cut:], pos + cut
        if buf and pos - start + len(body) > CHUNK:  # the padding counts too
            yield start, buf
            buf = ""
        if not buf:
            start = pos
            buf = body
        else:
            buf += " " * (pos - start - len(buf)) + body  # keep offsets: pad what was skipped
    if buf.strip():
        yield start, buf


def installed_snapshot(cache):
    """The model's files at the commit setup.py fetched (installed.json's
    revision, else the pinned one), so the server loads exactly those; None
    when the cache doesn't hold that commit (it then loads what it holds)."""
    try:
        with open(os.path.join(cache, "installed.json"), encoding="utf-8") as f:
            revision = json.load(f).get("revision") or MODEL_REVISION
    except (OSError, ValueError):
        revision = MODEL_REVISION
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return None
    path = os.path.join(os.environ.get("HF_HOME") or os.path.join(cache, "hf"), "hub",
                        "models--" + MODEL.replace("/", "--"), "snapshots", revision)
    return path if os.path.isdir(path) else None


class Gliner:
    def __init__(self, cache):
        os.environ.setdefault("HF_HOME", os.path.join(cache, "hf"))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # setup downloaded it; never fetch at run time
        from gliner import GLiNER

        self.name = MODEL
        self.model = GLiNER.from_pretrained(installed_snapshot(cache) or MODEL)

    def predict(self, chunks, labels, threshold):
        run = getattr(self.model, "inference", None) or getattr(self.model, "batch_predict_entities", None)
        if run:
            return run(chunks, labels, threshold=threshold)
        return [self.model.predict_entities(c, labels, threshold=threshold) for c in chunks]


class Fake:
    """Tests only: a few fixed rules standing in for the model, so the mod and
    the server can be tested where the real model can't be downloaded."""

    NAMES = ["Lars Holm", "Mette Sørensen", "Peter Jensen", "Anna Berg"]
    RULES = [
        ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
        ("phone number", re.compile(r"\+\d{2}(?: ?\d{2}){4}")),
        ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?\d{4}){3,5}(?: ?\d{1,3})?\b")),
    ]

    def __init__(self, cache):
        self.name = "fake (tests only)"

    def predict(self, texts, labels, threshold):
        results = []
        for t in texts:
            found = []
            for label, rx in self.RULES:
                if label in labels:
                    found += [{"text": m.group(), "label": label, "start": m.start(), "end": m.end(), "score": 0.99} for m in rx.finditer(t)]
            if "person" in labels:
                for n in self.NAMES:
                    for m in re.finditer(re.escape(n), t):
                        found.append({"text": n, "label": "person", "start": m.start(), "end": m.end(), "score": 0.95})
            results.append(found)
        return results


class Gate:
    """One model call at a time. Of the requests waiting, the one with the
    fewest chunks left goes next, so a prompt isn't stuck behind a long file."""

    def __init__(self):
        self.cond = threading.Condition()
        self.busy = False
        self.waiting = []
        self.seq = itertools.count()

    def run(self, size, fn):
        key = (size, next(self.seq))
        with self.cond:
            heapq.heappush(self.waiting, key)
            while self.busy or self.waiting[0] != key:
                self.cond.wait()
            heapq.heappop(self.waiting)
            self.busy = True
        try:
            return fn()
        finally:
            with self.cond:
                self.busy = False
                self.cond.notify_all()


class Detector:
    """Splits texts into chunks, answers the chunks it has seen before from a
    cache (Claude Code sends the same system prompt and reminders every
    turn), and runs the rest through the model, a few chunks at a time."""

    def __init__(self, backend):
        self.backend = backend
        self.name = backend.name
        self.gate = Gate()
        self.cache = {}
        self.cache_lock = threading.Lock()

    def detect(self, texts, labels, threshold, cancelled=lambda: False):
        parts = [(i, off, chunk) for i, t in enumerate(texts) for off, chunk in pieces(t) if chunk.strip()]
        key = (tuple(labels), threshold)
        with self.cache_lock:
            found = {c: self.cache.get((key, c)) for _, _, c in parts}
        todo = list(dict.fromkeys(c for c, ents in found.items() if ents is None))
        for n in range(0, len(todo), BATCH):
            if cancelled():  # the mod gave up waiting: free the model for the next request
                raise RuntimeError("cancelled")
            batch = todo[n:n + BATCH]
            ents = self.gate.run(len(todo) - n, lambda: self.backend.predict(batch, labels, threshold))
            with self.cache_lock:
                for c, e in zip(batch, ents):
                    found[c] = e
                    if len(self.cache) >= CACHE_SIZE:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[(key, c)] = e
        results = [[] for _ in texts]
        for i, off, c in parts:
            for e in found[c]:
                results[i].append({
                    "text": e["text"], "label": e["label"],
                    "start": e["start"] + off, "end": e["end"] + off,
                    "score": round(float(e["score"]), 3),
                })
        return results


class Jobs:
    """Requests run in the background: Claude Code ends a plugin's HTTP request
    after 30 s, and a long file or a dense scanned page can take longer on a
    slow machine. The mod starts a job and asks for it until it is done, each
    request short. Finished jobs are kept 10 minutes."""

    KEEP = 600

    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        self.ids = itertools.count(1)

    def start(self, work):
        job = str(next(self.ids))
        with self.lock:
            now = time.time()
            for k in [k for k, j in self.jobs.items() if j["done"] and now - j["at"] > self.KEEP]:
                del self.jobs[k]
            self.jobs[job] = {"done": False, "at": now, "event": threading.Event(), "cancelled": threading.Event()}
        cancelled = self.jobs[job]["cancelled"]

        def run():
            try:
                status, body = work(cancelled.is_set)
            except Exception as err:  # never leave a job unfinished
                status, body = 500, {"error": f"{type(err).__name__}: {err}"}
            with self.lock:
                event = self.jobs[job]["event"]
                self.jobs[job] = {"done": True, "at": time.time(), "status": status, "body": body, "event": event, "cancelled": cancelled}
            event.set()

        threading.Thread(target=run, daemon=True).start()
        return job

    def cancel(self, job):
        """The mod gave up on the job: it stops between steps and writes no file."""
        with self.lock:
            j = self.jobs.pop(job, None)
        if j:
            j["cancelled"].set()
        return j is not None

    def get(self, job, wait=0.0):
        """The job, once done or after `wait` seconds (a long poll: the caller
        needs no timer of its own between asks)."""
        with self.lock:
            j = self.jobs.get(job)
        if j and not j["done"] and wait > 0:
            j["event"].wait(min(wait, 25))
        with self.lock:
            j = self.jobs.get(job)
            if j and j["done"]:
                del self.jobs[job]  # answered once
            return j


MEDIA_FILE = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?|pdf)$", re.I)
COPY_NAME = re.compile(r"^read-\d+-\d+\.(png|pdf)$")


def input_path(path):
    """A file the mod asks to read: an image or a PDF, nothing else."""
    if not isinstance(path, str) or not os.path.isabs(path) or not MEDIA_FILE.search(path):
        raise ValueError("path must be an absolute path to an image or a PDF")
    return path


def copy_path(path):
    """Where the mod asks for a covered copy: read-<time>-<n>.png|pdf in a
    .claude/pii-mask folder, so no other file can be written or replaced."""
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError("out must be an absolute path")
    folder, name = os.path.split(os.path.normpath(path))
    parts = os.path.normpath(folder).replace("\\", "/").split("/")
    if not COPY_NAME.match(name) or parts[-2:] != [".claude", "pii-mask"] or os.path.islink(path):
        raise ValueError("out must be .claude/pii-mask/read-<time>-<n>.png or .pdf")
    return path


def make_handler(backend, state, key, media=None):
    """The request handler. `key` is the secret every request but /health must carry."""
    jobs = Jobs()

    def detect(req, cancelled=lambda: False):
        texts, labels = req["texts"], req["labels"]
        threshold = float(req.get("threshold", 0.5))
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("texts must be a list of strings")
        return 200, {"results": backend.detect(texts, labels, threshold, cancelled)}

    def media_work(kind, req, status, cancelled=lambda: False):
        """POST /media/pdf {data, render?, maxPages?}, /media/ocr {data},
        /media/redact {data, boxes, faces?, out?}, /media/topdf {images, out}; data is
        base64 both ways, or `path`, an image or PDF on this machine."""
        data = None if kind == "topdf" else (
            open(input_path(req["path"]), "rb").read() if req.get("path") else base64.b64decode(req["data"]))
        out_path = copy_path(req["out"]) if kind == "topdf" or req.get("out") else None
        if kind == "topdf":
            pdf = media.to_pdf([base64.b64decode(i) for i in req["images"]])
            if cancelled():
                return 499, {"error": "cancelled"}
            write_file(out_path, pdf)
            return 200, {"out": out_path, "bytes": len(pdf)}
        if kind == "pdf":
            out = media.pdf(data, render=bool(req.get("render")), max_pages=int(req.get("maxPages", 5)))
            out["images"] = [base64.b64encode(i).decode() for i in out["images"]]
            return 200, out
        if kind == "ocr":
            return 200, media.ocr(data)
        out = media.redact(data, req.get("boxes") or [], blur_faces=bool(req.get("faces")) and status.get("faces"))
        if cancelled():  # the mod gave up waiting: no copy appears after the Read went on without it
            return 499, {"error": "cancelled"}
        if out_path:  # written where the caller asked (a copy for Claude Code's Read tool)
            write_file(out_path, out.pop("data"))
            out["out"] = out_path
        else:
            out["data"] = base64.b64encode(out["data"]).decode()
        return 200, out

    def guarded(work):
        try:
            return work()
        except (ValueError, KeyError, TypeError) as err:
            return 400, {"error": str(err)}
        except Exception as err:  # a file the libraries can't read
            return 422, {"error": f"{type(err).__name__}: {err}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # never log what was sent
            pass

        def refused(self, need_key=True):
            """Answers 403 and returns True for a request the mod didn't make: one
            from a web page (an Origin header, or another site's name as Host:
            DNS rebinding), or, where `need_key`, one without the key."""
            port = self.server.server_address[1]
            host = (self.headers.get("host") or "").lower()
            if self.headers.get("origin") or host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.answer(403, {"error": "forbidden"})
                return True
            if need_key and not hmac.compare_digest(self.headers.get("x-pii-mask-key") or "", key):
                self.answer(401, {"error": "a key is needed"})
                return True
            return False

        def answer(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            if self.refused(need_key=path != "/health"):
                return
            state["last"] = time.time()
            if path == "/health":
                body = {"app": "pii-mask", "ok": True, "model": backend.name, "version": VERSION, "peak_rss_mb": peak_rss_mb()}
                if params.get("nonce"):
                    body["proof"] = hashlib.sha256(f"{key}:{params['nonce']}".encode()).hexdigest()
                if media is not None:
                    body["media"] = media.status()
                self.answer(200, body)
            elif self.path.startswith("/job/"):
                job, _, query = self.path[len("/job/"):].partition("?")
                wait = float(dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("wait", 0) or 0)
                j = jobs.get(job, wait)
                if j is None:
                    self.answer(404, {"error": "no such job"})
                elif not j["done"]:
                    self.answer(200, {"done": False})
                else:
                    self.answer(200, {"done": True, "status": j["status"], "body": j["body"]})
            else:
                self.answer(404, {"error": "not found"})

        def do_POST(self):
            if self.refused():
                return
            state["last"] = time.time()
            if self.path.startswith("/job/") and self.path.endswith("/cancel"):
                return self.answer(200, {"cancelled": jobs.cancel(self.path[len("/job/"):-len("/cancel")])})
            if self.path == "/stop":  # a newer version of the mod replaces this server
                self.answer(200, {"stopping": True})
                if state.get("stop"):
                    state["stop"]()
                return
            if self.path == "/detect":
                work = detect
            elif self.path.startswith("/media/"):
                kind = self.path[len("/media/"):]
                if media is None or kind not in ("pdf", "ocr", "redact", "topdf"):
                    return self.answer(404, {"error": "not found"})
                need = {"pdf": "pdf", "ocr": "images", "redact": "images", "topdf": "pdf"}[kind]
                status = media.status()
                if not status.get(need) and not (kind == "redact" and status.get("faces")):
                    return self.answer(503, {"error": f"{need} support is not installed"})
                work = lambda req, cancelled=lambda: False, kind=kind, status=status: media_work(kind, req, status, cancelled)
            else:
                return self.answer(404, {"error": "not found"})
            if (self.headers.get("content-type") or "").split(";")[0].strip().lower() != "application/json":
                return self.answer(415, {"error": "content-type must be application/json"})
            length = int(self.headers.get("content-length") or 0)
            if length > MAX_BODY:
                return self.answer(413, {"error": "request too large"})
            try:
                req = json.loads(self.rfile.read(length))
            except ValueError as err:
                return self.answer(400, {"error": str(err)})

            def run(cancelled=lambda: False):
                result = guarded(lambda: work(req, cancelled))
                state["last"] = time.time()
                return result

            if not isinstance(req, dict):
                return self.answer(400, {"error": "the body must be a JSON object"})
            if req.get("async"):  # a job: the answer comes from GET /job/<id>
                return self.answer(202, {"job": jobs.start(run)})
            self.answer(*run())

    return Handler


def load_key(cache):
    """The key only this user's mod can read: <cache>/token, made once with
    permissions for this user alone."""
    path = os.path.join(cache, "token")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as f:
        key = f.read().strip()
    if len(key) < 32:
        raise SystemExit(f"pii-mask: {path} holds no valid key; delete it and start again")
    return key


READ_COPIES_KEPT = 20


def write_file(path, data):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    # Copies made for Claude Code's Read tool: the newest few are kept, so you can see what Claude got.
    if os.path.basename(path).startswith("read-"):
        copies = sorted((f for f in os.listdir(folder) if f.startswith("read-")), key=lambda f: os.path.getmtime(os.path.join(folder, f)))
        for old in copies[:-READ_COPIES_KEPT]:
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass


class Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR would let a second server take the same port.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # HTTPServer looks up the host's name (socket.getfqdn), which can take
        # 30 s or more on some Macs; this server only ever listens on 127.0.0.1.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


# Windows process flags: no console, its own process group, and out of the
# caller's job, so it keeps running when the session that started it ends.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def detach_windows(args, cache):
    """Windows has no fork: start this script again as a detached process
    (without --daemon) writing to server.log, and return at once."""
    argv = [sys.executable, os.path.abspath(__file__), "--cache", cache, "--port", str(args.port), "--idle", str(args.idle)]
    if args.fake:
        argv.append("--fake")
    log = open(os.path.join(cache, "server.log"), "ab")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             close_fds=True, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
        except OSError:  # the job doesn't allow breaking away
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             close_fds=True, creationflags=flags)
    finally:
        log.close()


def daemonize(log_path):
    """Detaches from the mod: the call that started it returns at once, and the
    server keeps running when that session ends."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", required=True)
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--idle", type=int, default=1800, help="seconds without requests before it stops (default 1800)")
    p.add_argument("--daemon", action="store_true", help="detach and keep running after the caller exits")
    p.add_argument("--fake", action="store_true", help="tests only: fixed rules instead of the model")
    args = p.parse_args()
    cache = os.path.abspath(os.path.expanduser(args.cache))
    os.makedirs(cache, exist_ok=True)

    # Bind first, so a second start (another session, same moment) exits quietly.
    state = {"last": time.time()}
    try:
        server = Server(("127.0.0.1", args.port), BaseHTTPRequestHandler)
    except OSError:
        print(f"pii-mask: port {args.port} is in use; another server is probably running", file=sys.stderr)
        return 0
    key = load_key(cache)
    if args.daemon and os.name == "nt":
        server.server_close()  # the detached copy binds it again
        detach_windows(args, cache)
        return 0
    if args.daemon:
        daemonize(os.path.join(cache, "server.log"))

    started = time.time()
    backend = Detector(Fake(cache) if args.fake else Gliner(cache))
    backend.detect(["Warm-up: Anna Berg, anna@example.com."], ["person", "email"], 0.5)
    from media import Media

    media = Media(cache)
    if media.status()["images"]:
        media.warm_up()  # the OCR model takes seconds to load; not on the first image's clock
    server.RequestHandlerClass = make_handler(backend, state, key, media)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pii-mask model server: {backend.name} ready in "
          f"{time.time() - started:.1f} s on 127.0.0.1:{args.port}", flush=True)

    def watchdog():
        while True:
            time.sleep(30)
            if time.time() - state["last"] > args.idle:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} idle for {args.idle} s, stopping", flush=True)
                server.shutdown()
                return

    threading.Thread(target=watchdog, daemon=True).start()

    def stop():
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} asked to stop (a newer version starts)", flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    state["stop"] = stop
    server.serve_forever()
    # Free the port at once and skip the slow teardown of PyTorch and the OCR model: a newer
    # version that asked this one to stop starts its own on the same port right after.
    server.server_close()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
