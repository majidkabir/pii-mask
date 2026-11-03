"""Microsoft Presidio Image Redactor (default: Tesseract OCR + Presidio's analyzer
with en_core_web_lg) on the rendered pages; the boxes it would redact.

    python presidio_image.py PAGES_DIR gold.json out.json clean|scan
"""
import json
import sys
import time

from PIL import Image
from presidio_image_redactor import ImageAnalyzerEngine

pages_dir, gold_file, out_file, kind = sys.argv[1:5]
engine = ImageAnalyzerEngine()
out = {}
for p in json.load(open(gold_file))[kind]:
    t = time.time()
    img = Image.open(f"{pages_dir}/{p['page']}")
    found = engine.analyze(img)
    out[p["page"]] = {"boxes": [[r.left, r.top, r.left + r.width, r.top + r.height] for r in found],
                      "ms": int((time.time() - t) * 1000), "ocrWidth": img.width, "ocrHeight": img.height}
json.dump(out, open(out_file, "w"))
print(len(out), kind, "pages")
