"""Scores pii-mask and Presidio on presidio-research's synth_dataset_v2.json with
presidio-evaluator's own SpanEvaluator, set up as its notebook 4 does
(iou_threshold=0.75, F2, binary PII vs O). pii-mask's spans come from run.ts.

    python eval_presidio.py synth_piimask.jsonl [synth_dataset_v2.json]
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from presidio_analyzer import AnalyzerEngine
from presidio_evaluator import InputSample, span_to_tag
from presidio_evaluator.entity_mapping import CanonicalMapper
from presidio_evaluator.evaluation import SpanEvaluator
from presidio_evaluator.models import BaseModel, PresidioAnalyzerWrapper

HERE = Path(__file__).parent
DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "data/presidio-research/data/synth_dataset_v2.json"
# pii-mask's placeholder labels in Presidio's names, so the mapper knows them.
NAMES = {"PERSON": "PERSON", "EMAIL": "EMAIL_ADDRESS", "PHONE": "PHONE_NUMBER", "ADDRESS": "STREET_ADDRESS",
         "CARD": "CREDIT_CARD", "IBAN": "IBAN_CODE", "ID_NUMBER": "US_SSN", "IP": "IP_ADDRESS",
         "ACCOUNT": "US_BANK_NUMBER", "PASSPORT": "US_PASSPORT", "BIRTHDATE": "DATE_TIME", "SECRET": "PASSWORD",
         "MAC": "MAC_ADDRESS", "LOCATION": "LOCATION", "NUMBER": "ID"}


class PiiMask(BaseModel):
    def __init__(self, spans_file):
        super().__init__()
        self.spans = {}
        for line in open(spans_file):
            r = json.loads(line)
            self.spans[int(r["id"])] = r["spans"]
        self.i = 0
        self.name = "pii-mask"

    def predict(self, sample, **kwargs):
        spans = self.spans[sample.sample_id] if sample.sample_id is not None else []
        return span_to_tag(scheme="IO", text=sample.full_text, starts=[s for s, _, _ in spans],
                           ends=[e for _, e, _ in spans], tags=[NAMES.get(l, l) for _, _, l in spans],
                           tokens=sample.tokens, scores=[1.0] * len(spans))

    def batch_predict(self, dataset, **kwargs):
        return [self.predict(s) for s in dataset]


def score(model, dataset):
    df = model.predict_dataset(dataset)
    mapper = CanonicalMapper()
    mapper.analyze(df)
    mapped = mapper.get_mapped_results_dataframe()
    res = SpanEvaluator(iou_threshold=0.75).calculate_hierarchical_scores(mapped, beta=2)
    b = res["binary"]
    return {"precision": round(b.pii_precision, 3), "recall": round(b.pii_recall, 3), "f2": round(b.pii_f, 3)}


def hidden_by_type(dataset, spans_of):
    """Per gold type: the share of its characters inside a masked span (any label)."""
    tot, hid = Counter(), Counter()
    for s in dataset:
        masked = set()
        for a, b in spans_of(s):
            masked.update(range(a, b))
        for sp in s.spans:
            chars = [i for i in range(sp.start_position, sp.end_position) if not s.full_text[i].isspace()]
            tot[sp.entity_type] += len(chars)
            hid[sp.entity_type] += sum(1 for i in chars if i in masked)
    return {t: round(hid[t] / tot[t], 3) for t in sorted(tot, key=lambda t: -tot[t])}


def main():
    dataset = InputSample.read_dataset_json(DATA)
    for i, s in enumerate(dataset):
        s.sample_id = i
    piimask = PiiMask(sys.argv[1])
    analyzer = AnalyzerEngine(default_score_threshold=0.4)
    presidio = PresidioAnalyzerWrapper(analyzer_engine=analyzer)
    out = {"pii-mask": score(piimask, dataset), "presidio": score(presidio, dataset)}
    pres_spans = {}
    for i, s in enumerate(dataset):
        pres_spans[i] = [(r.start, r.end) for r in analyzer.analyze(text=s.full_text, language="en")]
    out["hidden_by_type"] = {
        "pii-mask": hidden_by_type(dataset, lambda s: [(a, b) for a, b, _ in piimask.spans[s.sample_id]]),
        "presidio": hidden_by_type(dataset, lambda s: pres_spans[s.sample_id]),
    }
    print(json.dumps(out, indent=1))
    json.dump(out, open(HERE / "presidio_results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
