"""Scores image redaction boxes against the gold word boxes of the rendered pages.

- A gold word is hidden when at least 70% of its box is under redaction boxes.
- Hidden share: per label, and for the kinds pii-mask is meant to hide.
- Box precision: the share of redaction boxes with at least half their area on
  labelled words (gold boxes widened by 10 px to take in spaces and punctuation).

    python score_image.py gold_sample.json name=boxes.json [name=boxes.json ...]
"""
import json
import sys
from collections import Counter

import numpy as np

W, H = 1240, 1754
PERSONAL = {"PERSON", "STREET_ADDRESS", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IBAN_CODE", "US_SSN",
            "IP_ADDRESS", "ZIP_CODE", "US_DRIVER_LICENSE"}


def area_mask(boxes, pad=0):
    m = np.zeros((H, W), bool)
    for x0, y0, x1, y1 in boxes:
        m[max(0, int(y0) - pad):min(H, int(y1) + pad), max(0, int(x0) - pad):min(W, int(x1) + pad)] = True
    return m


def score(gold_pages, system):
    hid, tot = Counter(), Counter()
    good_boxes = all_boxes = 0
    ms = []
    for p in gold_pages:
        r = system[p["page"]]
        sx, sy = W / r["ocrWidth"], H / r["ocrHeight"]
        boxes = [[x0 * sx, y0 * sy, x1 * sx, y1 * sy] for x0, y0, x1, y1 in r["boxes"]]
        covered = area_mask(boxes)
        part = "tab" if "tab-" in p["page"] else "presidio"
        for g in p["gold"]:
            x0, y0, x1, y1 = (int(round(v)) for v in g["box"])
            region = covered[max(0, y0):y1, max(0, x0):x1]
            ok = region.size > 0 and region.mean() >= 0.7
            for key in (f"{part}:{g['type']}", f"{part}:ALL") + ((f"{part}:PERSONAL",) if part == "presidio" and g["type"] in PERSONAL else ()):
                hid[key] += ok
                tot[key] += 1
        goldm = area_mask([g["box"] for g in p["gold"]], pad=10)
        for x0, y0, x1, y1 in boxes:
            region = goldm[max(0, int(y0)):int(y1), max(0, int(x0)):int(x1)]
            all_boxes += 1
            good_boxes += region.size > 0 and region.mean() >= 0.5
        ms.append(r["ms"])
    return {"hidden": {k: round(hid[k] / tot[k], 3) for k in sorted(tot, key=lambda k: (k.split(':')[0], -tot[k]))},
            "words": dict(tot), "box_precision": round(good_boxes / max(1, all_boxes), 3), "boxes": all_boxes,
            "seconds_per_page": round(sum(ms) / len(ms) / 1000, 1)}


def main():
    gold = json.load(open(sys.argv[1]))
    out = {}
    for arg in sys.argv[2:]:
        name, path = arg.split("=")
        kind = "scan" if "scan" in name else "clean"
        out[name] = score(gold[kind], json.load(open(path)))
    print(json.dumps(out, indent=1))
    json.dump(out, open("image_results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
