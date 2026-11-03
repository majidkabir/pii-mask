#!/usr/bin/env python3
"""Performance of pii-mask's local server, measured the way the mod uses it.

    python perf.py [--port 8766] [--json out.json] [--pages DIR]

Needs a running server (after a session with the mod, or `server.py --daemon`).
Text: model time by input size, without and with the server's cache, and how
long a short prompt waits while a long file is being checked. Images and PDFs
(needs Pillow, as in the server's venv): OCR and drawing per kind of image, PDF
text and rendering. Memory: the server's peak, from /health. Each figure is the
median of 3 runs. Unique text per run, so the cache doesn't answer.
"""
import argparse
import base64
import io
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.request

LABELS = ["person", "email", "phone number", "address", "iban", "bank account number", "credit card number",
          "social security number", "national id number", "passport number", "date of birth", "ip address"]
FIRST = ["Anna", "Lars", "Sofie", "Erik", "Mette", "Jonas", "Ingrid", "Pekka", "Emma", "Lukas", "Maria", "Ahmed"]
LAST = ["Berg", "Holm", "Lindqvist", "Nilsson", "Sørensen", "Virtanen", "Johansson", "Schneider", "Larsen", "Khan"]
WORDS = ("the order was shipped from the warehouse after the payment cleared and the customer asked for an update "
         "about delivery times invoice reference attached please confirm the address below thanks for your patience").split()


def post(port, path, body, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(), {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read())


def text_of(size, rng):
    """Support-ticket text with a name, an email and a phone number every ~300 characters."""
    out = []
    while sum(len(s) + 1 for s in out) < size:
        f, l = rng.choice(FIRST), rng.choice(LAST)
        out.append(" ".join(rng.choice(WORDS) for _ in range(rng.randint(15, 30))).capitalize() + ".")
        out.append(f"Contact {f} {l} at {f.lower()}.{l.lower()}{rng.randint(1, 999)}@example.com or +45 {rng.randint(20, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)}.")
    return " ".join(out)[:size]


def timed(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def median3(make):
    return round(statistics.median(make() for _ in range(3)))


def text_section(port, rng):
    rows = []
    for size in (200, 1_000, 4_000, 16_000, 64_000):
        texts = [text_of(size, rng) for _ in range(3)]
        cold = [timed(lambda t=t: post(port, "/detect", {"texts": [t], "labels": LABELS, "threshold": 0.5})) for t in texts]
        warm = timed(lambda: post(port, "/detect", {"texts": [texts[0]], "labels": LABELS, "threshold": 0.5}))
        ms = statistics.median(cold)
        rows.append({"chars": size, "ms": round(ms), "chars_per_s": round(size / ms * 1000), "cached_ms": round(warm)})
    # A short prompt sent while a long file is being checked.
    small = text_of(200, rng)
    alone = timed(lambda: post(port, "/detect", {"texts": [text_of(200, rng)], "labels": LABELS, "threshold": 0.5}))
    big = threading.Thread(target=lambda: post(port, "/detect", {"texts": [text_of(32_000, rng)], "labels": LABELS, "threshold": 0.5}))
    big.start()
    time.sleep(0.5)
    behind = timed(lambda: post(port, "/detect", {"texts": [small], "labels": LABELS, "threshold": 0.5}))
    big.join()
    return {"by_size": rows, "short_prompt_alone_ms": round(alone), "short_prompt_behind_32k_file_ms": round(behind)}


def images(pages_dir):
    """Test images: a screenshot, a business card, a dense A4 page, a photo without text."""
    from PIL import Image, ImageDraw, ImageFont

    def font(n):
        try:
            return ImageFont.load_default(size=n)
        except TypeError:
            return ImageFont.load_default()

    rng = random.Random(3)
    shot = Image.new("RGB", (1280, 800), (246, 247, 249))
    d = ImageDraw.Draw(shot)
    for i in range(14):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        d.text((60, 60 + i * 50), f"#{1000 + i}  {f} {l}  {f.lower()}@example.com  +45 {rng.randint(20, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)}  paid", fill=(30, 30, 30), font=font(22))
    card = Image.new("RGB", (900, 260), "white")
    d = ImageDraw.Draw(card)
    d.text((300, 50), "Sofie Lindqvist", fill="black", font=font(36))
    d.text((300, 110), "Account manager", fill="black", font=font(26))
    d.text((300, 160), "sofie.lindqvist@example.dk", fill="black", font=font(26))
    page = None
    if pages_dir and os.path.exists(os.path.join(pages_dir, "scan", "tab-001-61744.jpg")):
        page = Image.open(os.path.join(pages_dir, "scan", "tab-001-61744.jpg")).convert("RGB")
    else:
        page = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(page)
        for i in range(40):
            d.text((110, 110 + i * 38), " ".join(rng.choice(WORDS) for _ in range(12)) + f" {rng.choice(FIRST)} {rng.choice(LAST)}", fill="black", font=font(24))
    photo = Image.effect_noise((1600, 1200), 40).convert("RGB")
    return {"screenshot 1280x800, 14 lines": shot, "business card 900x260": card, "dense A4 page (scanned)": page, "photo 1600x1200, no text": photo}


def png64(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def text_pdf(pages):
    lines = "BT /F1 11 Tf 72 740 Td 14 TL " + " ".join(f"(Line {i}: Contact Anna Berg at anna.berg@example.com about order {i}.) Tj T*" for i in range(45)) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{' '.join(f'{3 + i} 0 R' for i in range(pages))}] /Count {pages} >>"]
    for i in range(pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {3 + pages} 0 R /Resources << /Font << /F1 {4 + pages} 0 R >> >> >>")
    objs += [f"<< /Length {len(lines)} >>\nstream\n{lines}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return base64.b64encode(out).decode()


def media_section(port, pages_dir):
    rows = []
    for name, img in images(pages_dir).items():
        data = png64(img)
        post(port, "/media/ocr", {"data": data})  # the OCR model loaded, the image's first read out of the way
        ocr = median3(lambda: timed(lambda: post(port, "/media/ocr", {"data": data})))
        words = sum(len(l["words"]) for l in post(port, "/media/ocr", {"data": data})["lines"])
        red = median3(lambda: timed(lambda: post(port, "/media/redact", {"data": data, "boxes": [], "faces": True})))
        rows.append({"image": name, "words": words, "ocr_ms": ocr, "redact_and_faces_ms": red})
    pdfs = []
    for pages in (1, 20):
        data = text_pdf(pages)
        pdfs.append({"pdf": f"text PDF, {pages} page(s)", "ms": median3(lambda: timed(lambda: post(port, "/media/pdf", {"data": data})))})
    return {"images": rows, "pdfs": pdfs}


def table(res):
    out = ["| Text (chars) | Model, ms | Chars/s | Cached, ms |", "| --- | --- | --- | --- |"]
    out += [f"| {r['chars']:,} | {r['ms']:,} | {r['chars_per_s']:,} | {r['cached_ms']} |" for r in res["text"]["by_size"]]
    out += ["", f"Short prompt alone: {res['text']['short_prompt_alone_ms']} ms; behind a 32,000-character file: {res['text']['short_prompt_behind_32k_file_ms']} ms", ""]
    if "media" in res:
        out += ["| Image | Words | OCR, ms | Boxes + faces, ms |", "| --- | --- | --- | --- |"]
        out += [f"| {r['image']} | {r['words']} | {r['ocr_ms']:,} | {r['redact_and_faces_ms']} |" for r in res["media"]["images"]]
        out += [""] + [f"{p['pdf']}: {p['ms']} ms" for p in res["media"]["pdfs"]] + [""]
    out.append(f"Server peak memory: {res.get('peak_rss_mb', '?')} MB")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--json")
    p.add_argument("--pages", help="the benchmark's rendered pages, for a real scanned page")
    p.add_argument("--no-media", action="store_true")
    args = p.parse_args()
    rng = random.Random(11)
    health = get(args.port, "/health")
    res = {"server": {k: health.get(k) for k in ("model", "version")}, "text": text_section(args.port, rng)}
    if not args.no_media and (health.get("media") or {}).get("images"):
        res["media"] = media_section(args.port, args.pages)
    res["peak_rss_mb"] = get(args.port, "/health").get("peak_rss_mb")
    md = table(res)
    print(md)
    if args.json:
        json.dump(res, open(args.json, "w"), indent=1)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::notice title=performance::" + md.replace("%", "%25").replace("\n", "%0A"))


if __name__ == "__main__":
    sys.exit(main())
