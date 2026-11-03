"""Renders the two text benchmarks as A4 page images, keeping their labels as
word boxes, so an image redactor can be scored the way the text is.

- presidio: the 1,500 sentences of presidio-research's synth_dataset_v2.json,
  one after another with a blank line between, as many pages as they fill.
- tab: the first page of each of TAB's 127 test documents, with its DIRECT and
  QUASI mentions.

Each page is written twice: clean (as a PDF page rendered at 150 dpi), and
"scanned": turned up to 1 degree, blurred, with noise, saved as JPEG at 50.
A gold word is a \\w+ token inside a labelled span; its box is where it was drawn.

    python render.py OUT_DIR
"""
import json
import math
import os
import random
import re
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, MARGIN = 1240, 1754, 110  # A4 at 150 dpi
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"]
SIZE, LEAD = 24, 36
SKIP = {"mr", "mrs", "ms", "dr", "no", "nr", "about", "the", "of", "a", "an", "and", "in", "on", "at", "for", "to", "by"}
HERE = os.path.dirname(os.path.abspath(__file__))
random.seed(7)


class Page:
    def __init__(self, font_path):
        self.img = Image.new("RGB", (W, H), "white")
        self.draw = ImageDraw.Draw(self.img)
        self.font = ImageFont.truetype(font_path, SIZE)
        self.y = MARGIN
        self.gold = []  # {"type", "box"}

    def full(self, lines=1):
        return self.y + lines * LEAD > H - MARGIN

    def layout(self, text):
        """Lines of (start, end) char ranges: wrapped at spaces, broken at newlines."""
        lines = []
        for m in re.finditer(r"[^\n]*", text):
            if m.start() > 0 and text[m.start() - 1] != "\n" and m.group() == "":
                continue
            s, e = m.start(), m.end()
            cur = s
            while True:
                if self.font.getlength(text[cur:e].replace("\t", " ")) <= W - 2 * MARGIN:
                    lines.append((cur, e))
                    break
                cut = cur
                for sp in re.finditer(r" ", text[cur:e]):
                    if self.font.getlength(text[cur:cur + sp.start()]) > W - 2 * MARGIN:
                        break
                    cut = cur + sp.start()
                if cut == cur:  # one long word: hard cut
                    cut = cur + max(1, int(len(text[cur:e]) * (W - 2 * MARGIN) / self.font.getlength(text[cur:e])))
                lines.append((cur, cut))
                cur = cut + 1 if text[cut:cut + 1] == " " else cut
        return lines

    def write(self, text, spans, lines):
        """Draws the given lines of text; returns the char boxes {index: box}."""
        boxes = {}
        for s, e in lines:
            seg = text[s:e]
            self.draw.text((MARGIN, self.y), seg, fill=(25, 25, 25), font=self.font)
            for i in range(s, e):
                x0 = MARGIN + self.font.getlength(text[s:i])
                x1 = MARGIN + self.font.getlength(text[s:i + 1])
                boxes[i] = (x0, self.y + 4, x1, self.y + SIZE + 6)
            self.y += LEAD
        seen = {(g["type"], tuple(g["box"])) for g in self.gold}
        for s, e, kind in spans:
            for m in re.finditer(r"\w+", text[s:e]):
                if m.group().lower() in SKIP:  # as TAB's scorer: titles, "no", articles and prepositions
                    continue
                chars = [boxes[i] for i in range(s + m.start(), s + m.end()) if i in boxes]
                box = [min(c[0] for c in chars), min(c[1] for c in chars), max(c[2] for c in chars), max(c[3] for c in chars)] if chars else None
                if box and (kind, tuple(box)) not in seen:
                    seen.add((kind, tuple(box)))
                    self.gold.append({"type": kind, "box": box})
        return boxes


def scanned(img, gold):
    angle = random.uniform(-1.0, 1.0)
    out = img.rotate(angle, resample=Image.BICUBIC, fillcolor="white")
    out = out.filter(ImageFilter.GaussianBlur(0.7))
    arr = np.array(out).astype(np.int16) + np.random.default_rng(len(gold)).normal(0, 9, (H, W, 1)).astype(np.int16)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    # PIL turns counter-clockwise by `angle` about the centre: move each box's corners the same way.
    a = math.radians(-angle)
    cx, cy = W / 2, H / 2
    def turn(x, y):
        return (cx + (x - cx) * math.cos(a) - (y - cy) * math.sin(a), cy + (x - cx) * math.sin(a) + (y - cy) * math.cos(a))
    moved = []
    for g in gold:
        x0, y0, x1, y1 = g["box"]
        pts = [turn(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        moved.append({"type": g["type"], "box": [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]})
    return out, moved


def save(out_dir, name, page, index):
    os.makedirs(f"{out_dir}/clean", exist_ok=True)
    os.makedirs(f"{out_dir}/scan", exist_ok=True)
    page.img.save(f"{out_dir}/clean/{name}.png")
    img, gold = scanned(page.img, page.gold)
    img.save(f"{out_dir}/scan/{name}.jpg", quality=50)
    index["clean"].append({"page": f"clean/{name}.png", "gold": page.gold})
    index["scan"].append({"page": f"scan/{name}.jpg", "gold": gold})


def presidio_pages(out_dir, index):
    data = json.load(open(f"{HERE}/data/presidio-research/data/synth_dataset_v2.json"))
    n, page = 0, Page(FONTS[0])
    for r in data:
        text = r["full_text"]
        spans = [(s["start_position"], s["end_position"], s["entity_type"]) for s in r["spans"]]
        lines = page.layout(text)
        if page.full(len(lines) + 1):
            save(out_dir, f"presidio-{n:03d}", page, index)
            n += 1
            page = Page(FONTS[n % 2])
        page.write(text, spans, lines)
        page.y += LEAD // 2
    save(out_dir, f"presidio-{n:03d}", page, index)


def tab_pages(out_dir, index):
    docs = json.load(open(f"{HERE}/data/tab/echr_test.json"))
    for k, d in enumerate(docs):
        page = Page(FONTS[k % 2])
        text = d["text"]
        lines = page.layout(text)
        fit = lines[: (H - 2 * MARGIN) // LEAD]
        end = fit[-1][1]
        spans = set()
        for ann in d["annotations"].values():
            for m in ann["entity_mentions"]:
                if m["identifier_type"] in ("DIRECT", "QUASI") and m["end_offset"] <= end:
                    spans.add((m["start_offset"], m["end_offset"], m["entity_type"]))
        page.write(text, sorted(spans), fit)
        save(out_dir, f"tab-{d['doc_id']}", page, index)


def main():
    out_dir = sys.argv[1]
    index = {"clean": [], "scan": []}
    presidio_pages(out_dir, index)
    tab_pages(out_dir, index)
    json.dump(index, open(f"{out_dir}/gold.json", "w"))
    print({k: len(v) for k, v in index.items()}, "pages;", sum(len(p["gold"]) for p in index["clean"]), "gold words")


if __name__ == "__main__":
    main()
