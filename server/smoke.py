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
import base64
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

    media_note = ""
    if not args.fake:
        media = health.get("media") or {}
        print(f"media support: {media}")
        if not all(media.get(k) for k in ("pdf", "images", "faces")):
            fail(f"image and PDF support is missing: {media}", cache)
        media_note = check_media(cache, python)
    print("OK")
    if os.environ.get("GITHUB_ACTIONS"):
        labels_found = ", ".join(sorted({f["label"] for f in found}))
        print(f"::notice title=smoke test::{health.get('model')}: setup {timings['setup']:.0f} s, "
              f"start {timings['start']:.0f} s, detect {timings['detect']:.2f} s; found {labels_found}{media_note}")


SAMPLE = '''
import io, sys
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (900, 200), "white")
d = ImageDraw.Draw(img)
try:
    f = ImageFont.load_default(size=30)
except TypeError:
    f = ImageFont.load_default()
d.text((30, 30), "Customer: Sofie Lindqvist", fill="black", font=f)
d.text((30, 110), "Mail: sofie" + "@" + "example.dk", fill="black", font=f)
img.save(sys.argv[1])
'''


def text_pdf(lines):
    content = "BT /F1 14 Tf 72 720 Td 18 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def check_media(cache, python):
    """A PDF's text, the text and words in an image, and a redacted image, from the real libraries."""
    b64 = lambda data: base64.b64encode(data).decode()
    t = time.time()
    pdf = get("/media/pdf", json.dumps({"data": b64(text_pdf(["Invoice for Anna Berg", "Total 100"]))}).encode(), timeout=60)
    t_pdf = time.time() - t
    if pdf.get("scanned") or "Anna Berg" not in "".join(pdf.get("pages", [])):
        fail(f"the PDF's text was not read: {pdf}", cache)
    sample = os.path.join(cache, "smoke-sample.png")
    subprocess.run([python, "-c", SAMPLE, sample], check=True)
    image = open(sample, "rb").read()
    t = time.time()
    ocr = None
    for _ in range(3):  # the OCR model may still be loading in the background
        try:
            ocr = get("/media/ocr", json.dumps({"data": b64(image)}).encode(), timeout=120)
            break
        except OSError:
            time.sleep(5)
    t_ocr = time.time() - t
    text = "\n".join(l["text"] for l in (ocr or {}).get("lines", []))
    print(f"OCR in {t_ocr:.1f} s: {text!r}")
    if "Lindqvist" not in text or "@" not in text:
        fail(f"OCR did not read the sample: {text!r}", cache)
    words = [w for l in ocr["lines"] for w in l["words"]]
    box = next(w["box"] for w in words if "Lindqvist" in w["text"])
    t = time.time()
    red = get("/media/redact", json.dumps({"data": b64(image), "boxes": [{"box": box, "label": "PERSON_1"}], "faces": True}).encode(), timeout=60)
    t_red = time.time() - t
    if not base64.b64decode(red["data"]).startswith(b"\x89PNG"):
        fail("the redacted image is not a PNG", cache)
    print(f"media: pdf {t_pdf:.2f} s, ocr {t_ocr:.1f} s, redact {t_red:.2f} s")
    return f"; pdf {t_pdf:.2f} s, ocr {t_ocr:.1f} s, redact {t_red:.2f} s"



if __name__ == "__main__":
    main()
