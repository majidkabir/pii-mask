#!/usr/bin/env python3
"""pii-mask's local model server: GLiNER PII behind a small HTTP API.

The mod starts it when no session has (`--daemon`), and every session on the
machine shares it. It listens on 127.0.0.1 only, logs nothing it is sent, and
stops by itself after 30 minutes without requests.

    <cache>/venv/bin/python server/server.py --cache ~/.cache/pii-mask [--daemon]
    (Windows: <cache>\\venv\\Scripts\\python.exe, cache %LOCALAPPDATA%\\pii-mask)

API (JSON):
    GET  /health  -> {"app": "pii-mask", "ok": true, "model": ...}
    POST /detect  {"texts": [...], "labels": ["person", "email", ...], "threshold": 0.5}
                  -> {"results": [[{"text", "label", "start", "end", "score"}, ...], ...]}
"""

import argparse
import heapq
import itertools
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "urchade/gliner_multi_pii-v1"
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


class Gliner:
    def __init__(self, cache):
        os.environ.setdefault("HF_HOME", os.path.join(cache, "hf"))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # setup downloaded it; never fetch at run time
        from gliner import GLiNER

        self.name = MODEL
        self.model = GLiNER.from_pretrained(MODEL)

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

    def detect(self, texts, labels, threshold):
        parts = [(i, off, chunk) for i, t in enumerate(texts) for off, chunk in pieces(t) if chunk.strip()]
        key = (tuple(labels), threshold)
        with self.cache_lock:
            found = {c: self.cache.get((key, c)) for _, _, c in parts}
        todo = list(dict.fromkeys(c for c, ents in found.items() if ents is None))
        for n in range(0, len(todo), BATCH):
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


def make_handler(backend, state):

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # never log what was sent
            pass

        def answer(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state["last"] = time.time()
            if self.path == "/health":
                self.answer(200, {"app": "pii-mask", "ok": True, "model": backend.name})
            else:
                self.answer(404, {"error": "not found"})

        def do_POST(self):
            state["last"] = time.time()
            if self.path != "/detect":
                return self.answer(404, {"error": "not found"})
            length = int(self.headers.get("content-length") or 0)
            if length > MAX_BODY:
                return self.answer(413, {"error": "request too large"})
            try:
                req = json.loads(self.rfile.read(length))
                texts, labels = req["texts"], req["labels"]
                threshold = float(req.get("threshold", 0.5))
                if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                    raise ValueError("texts must be a list of strings")
            except (ValueError, KeyError, TypeError) as err:
                return self.answer(400, {"error": str(err)})
            results = backend.detect(texts, labels, threshold)
            state["last"] = time.time()
            self.answer(200, {"results": results})

    return Handler


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
    if args.daemon and os.name == "nt":
        server.server_close()  # the detached copy binds it again
        detach_windows(args, cache)
        return 0
    if args.daemon:
        daemonize(os.path.join(cache, "server.log"))

    started = time.time()
    backend = Detector(Fake(cache) if args.fake else Gliner(cache))
    backend.detect(["Warm-up: Anna Berg, anna@example.com."], ["person", "email"], 0.5)
    server.RequestHandlerClass = make_handler(backend, state)
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
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
