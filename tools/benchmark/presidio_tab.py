"""Presidio (default AnalyzerEngine, en_core_web_lg) on TAB's test set, as the TAB paper's baseline."""
import json, sys
from presidio_analyzer import AnalyzerEngine
docs = json.load(open(sys.argv[1]))
engine = AnalyzerEngine()
engine.nlp_engine.nlp["en"].max_length = 2_000_000
out = {}
for d in docs:
    out[d["doc_id"]] = [[r.start, r.end] for r in engine.analyze(text=d["text"], language="en")]
json.dump(out, open(sys.argv[2], "w"))
print(len(out), "docs")
