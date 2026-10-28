#!/usr/bin/env python3
"""End-to-end check of setup.py and server.py, run the way the mod runs them.

    python server/smoke.py --cache DIR --fake     # stand-in model, seconds
    python server/smoke.py --cache DIR            # real install and model, minutes

It runs the setup, starts the server with --daemon while capturing its output
(as Claude Code does: if the detached server kept the caller's pipes open, this
call would hang), waits for /health, asks /detect, and checks that a second
start returns at once. Works on macOS, Linux and Windows.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8766


def get(path, body=None, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body,
                                 headers={"content-type": "application/json"} if body else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def venv_python(cache):
    if os.name == "nt":
        return os.path.join(cache, "venv", "Scripts", "python.exe")
    return os.path.join(cache, "venv", "bin", "python")


def fail(msg, cache):
    report = [f"FAILED: {msg}"]
    for name in ("setup.log", "server.log"):
        path = os.path.join(cache, name)
        if os.path.exists(path):
            report.append(f"--- last lines of {name}")
            report += [l.rstrip() for l in open(path, encoding="utf-8", errors="replace").readlines()[-30:]]
    print("\n".join(report))
    if os.environ.get("GITHUB_ACTIONS"):  # an annotation, readable through the API without the log
        text = "\n".join(report)[-6000:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=smoke test::{text}")
    sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", required=True)
    p.add_argument("--fake", action="store_true")
    p.add_argument("--start-limit", type=int, default=300, help="seconds to wait for the server")
    args = p.parse_args()
    cache = os.path.abspath(args.cache)
    fake = ["--fake"] if args.fake else []

    t = time.time()
    setup = subprocess.run([sys.executable, os.path.join(HERE, "setup.py"), "--cache", cache, *fake],
                           capture_output=True, text=True)
    print(setup.stdout.replace("\r", ""))
    if setup.returncode != 0:
        fail(f"setup exited with {setup.returncode}", cache)
    timings = {"setup": time.time() - t}
    print(f"setup: {timings['setup']:.0f} s")

    python = sys.executable if args.fake else venv_python(cache)
    start = [python, os.path.join(HERE, "server.py"), "--daemon", "--port", str(PORT), "--cache", cache, *fake]
    t = time.time()
    try:
        subprocess.run(start, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        fail("starting the server with --daemon did not return; the caller would hang", cache)
    print(f"--daemon returned in {time.time() - t:.1f} s")

    t = time.time()
    while True:
        try:
            health = get("/health")
            break
        except OSError:
            if time.time() - t > args.start_limit:
                fail(f"no /health answer within {args.start_limit} s", cache)
            time.sleep(1)
    timings["start"] = time.time() - t
    print(f"server ready in {timings['start']:.0f} s: {health}")
    if health.get("app") != "pii-mask":
        fail(f"unexpected /health answer {health}", cache)

    email = "sofie.lindqvist" + "@" + "example.dk"
    text = f"Please send the contract to Sofie Lindqvist at {email}. She lives in Hellerup."
    labels = ["person", "email", "phone number", "address"]
    t = time.time()
    found = get("/detect", json.dumps({"texts": [text], "labels": labels, "threshold": 0.5}).encode(), timeout=120)["results"][0]
    timings["detect"] = time.time() - t
    print(f"detect in {timings['detect']:.1f} s: {json.dumps(found, ensure_ascii=False)}")
    if args.fake:
        if not any(f["label"] == "email" and f["text"] == email for f in found):
            fail("the fake model did not find the email", cache)
    elif not any(f["label"] == "person" and "Lindqvist" in f["text"] for f in found):
        fail("the model did not find the person", cache)

    t = time.time()
    again = subprocess.run(start, capture_output=True, text=True, timeout=30)
    if again.returncode != 0 or time.time() - t > 15:
        fail(f"a second start did not exit quietly ({again.returncode}, {again.stderr.strip()})", cache)
    print("second start exited quietly")
    print("OK")
    if os.environ.get("GITHUB_ACTIONS"):
        labels_found = ", ".join(sorted({f["label"] for f in found}))
        print(f"::notice title=smoke test::{health.get('model')}: setup {timings['setup']:.0f} s, "
              f"start {timings['start']:.0f} s, detect {timings['detect']:.2f} s; found {labels_found}")


if __name__ == "__main__":
    main()
