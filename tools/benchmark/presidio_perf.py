"""Presidio on the same texts and images as perf.py, for comparison: AnalyzerEngine
(en_core_web_lg) by text size, and ImageAnalyzerEngine (Tesseract) per image.
Each figure is the median of 3 runs, after one warm-up.

    python presidio_perf.py [--pages DIR]
"""
import argparse
import json
import random
import statistics
import time

from presidio_analyzer import AnalyzerEngine
from presidio_image_redactor import ImageAnalyzerEngine

from perf import images, text_of


def ms(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pages")
    args = p.parse_args()
    rng = random.Random(11)
    t = time.perf_counter()
    engine = AnalyzerEngine()
    engine.nlp_engine.nlp["en"].max_length = 2_000_000
    load = (time.perf_counter() - t) * 1000
    engine.analyze(text="warm up Anna Berg", language="en")
    text = []
    for size in (200, 1_000, 4_000, 16_000, 64_000):
        runs = [ms(lambda: engine.analyze(text=text_of(size, rng), language="en")) for _ in range(3)]
        m = statistics.median(runs)
        text.append({"chars": size, "ms": round(m), "chars_per_s": round(size / m * 1000)})
    img_engine = ImageAnalyzerEngine(analyzer_engine=engine)
    imgs = []
    for name, img in images(args.pages).items():
        img_engine.analyze(img)
        imgs.append({"image": name, "ms": round(statistics.median(ms(lambda: img_engine.analyze(img)) for _ in range(3)))})
    res = {"load_ms": round(load), "text": text, "images": imgs}
    print(json.dumps(res, indent=1))
    json.dump(res, open("presidio_perf.json", "w"), indent=1)


if __name__ == "__main__":
    main()
