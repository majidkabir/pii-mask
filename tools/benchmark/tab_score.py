"""TAB's evaluation measures, written from the definitions in TAB's evaluation.py
(Pilán et al. 2022) without the BERT weighting:

- entity recall (direct / quasi): an entity counts only if every mention that
  needs masking is masked; micro-averaged over annotators.
- mention and token recall over DIRECT+QUASI, token recall per entity type.
- uniform token and mention precision: a system span counts for each annotator
  whose masked mention fully covers it.
A mention counts as masked when every character is covered, ignoring spaces,
punctuation, and tokens that are ADP/PART/CCONJ/DET or "mr", "mrs", "no"...

    python tab_score.py echr_test.json system1.json [system2.json ...]
"""
import json
import re
import sys
from collections import defaultdict

import intervaltree
import spacy

POS_TO_IGNORE = {"ADP", "PART", "CCONJ", "DET"}
TOKENS_TO_IGNORE = {"mr", "mrs", "ms", "no", "nr", "about"}
CHARS_TO_IGNORE = set(" ,.-;:/&()[]–'\" ’“”")


class Doc:
    def __init__(self, d, nlp):
        self.id, self.text = d["doc_id"], d["text"]
        self.sp = nlp(self.text)
        self.entities = []  # dicts: annotator, mentions, mask_flags, need, direct, type
        for annotator, ann in d["annotations"].items():
            by_id = {}
            for m in ann["entity_mentions"]:
                need = m["identifier_type"] in ("DIRECT", "QUASI")
                e = by_id.get(m["entity_id"])
                if e is None:
                    e = by_id[m["entity_id"]] = {"annotator": annotator, "mentions": [], "flags": [], "need": need,
                                                 "direct": m["identifier_type"] == "DIRECT", "type": m["entity_type"]}
                e["mentions"].append((m["start_offset"], m["end_offset"]))
                e["flags"].append(need)
            for e in by_id.values():
                if set(e["flags"]) != {e["need"]}:
                    e["need"] = True
            self.entities += by_id.values()
        self.annotators = {e["annotator"] for e in self.entities}
        self.tree = intervaltree.IntervalTree()
        for e in self.entities:
            if e["need"]:
                for (s, t), f in zip(e["mentions"], e["flags"]):
                    if f:
                        self.tree[s:t] = e["annotator"]

    def to_mask(self, direct=True, quasi=True):
        return [e for e in self.entities if e["need"] and ((direct and e["direct"]) or (quasi and not e["direct"]))]

    def mention_masked(self, covered, s, t):
        left = set(range(s, t)) - covered
        if left:
            span = self.sp.char_span(s, t, alignment_mode="expand")
            for tok in span or []:
                if tok.pos_ in POS_TO_IGNORE or tok.lower_ in TOKENS_TO_IGNORE:
                    left -= set(range(tok.idx, tok.idx + len(tok)))
        left = {i for i in left if self.text[i] not in CHARS_TO_IGNORE}
        return not left

    def tokens(self, s, t):
        for m in re.finditer(r"\w+", self.text[s:t]):
            yield s + m.start(), s + m.end()

    def annotators_for(self, s, t):
        return {a for (ms, me, a) in self.tree[s:t] if ms <= s and me >= t}


def score(docs, system):
    entity = {"direct": [0, 0], "quasi": [0, 0]}
    mention, token = [0, 0], [0, 0]
    by_type = defaultdict(lambda: [0, 0])
    prec = {"token": [0.0, 0.0], "mention": [0.0, 0.0]}
    for doc in docs:
        spans = [tuple(x) for x in system.get(doc.id, [])]
        covered = {i for s, t in spans for i in range(s, t)}
        for kind, args in (("direct", (True, False)), ("quasi", (False, True))):
            for e in doc.to_mask(*args):
                ok = all(doc.mention_masked(covered, s, t) or not f for (s, t), f in zip(e["mentions"], e["flags"]))
                entity[kind][0] += ok
                entity[kind][1] += 1
        for e in doc.to_mask():
            for s, t in e["mentions"]:
                mention[0] += doc.mention_masked(covered, s, t)
                mention[1] += 1
                for a, b in doc.tokens(s, t):
                    hit = doc.mention_masked(covered, a, b)
                    token[0] += hit
                    token[1] += 1
                    by_type[e["type"]][0] += hit
                    by_type[e["type"]][1] += 1
        n = len(doc.annotators)
        for level in ("token", "mention"):
            units = [u for s, t in spans for u in (doc.tokens(s, t) if level == "token" else [(s, t)])]
            for s, t in units:
                prec[level][0] += len(doc.annotators_for(s, t))
                prec[level][1] += n
    r = lambda p: round(p[0] / p[1], 3) if p[1] else 0.0
    return {
        "ER_direct": r(entity["direct"]), "ER_quasi": r(entity["quasi"]),
        "R_mention": r(mention), "R_token": r(token),
        "P_mention": r(prec["mention"]), "P_token": r(prec["token"]),
        "R_token_by_type": {k: r(v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1][1])},
        "counts": {"direct_entities": entity["direct"][1], "quasi_entities": entity["quasi"][1], "mentions": mention[1]},
    }


def main():
    nlp = spacy.load("en_core_web_md")
    gold = json.load(open(sys.argv[1]))
    docs = [Doc(d, nlp) for d in gold]
    out = {}
    for f in sys.argv[2:]:
        out[f] = score(docs, json.load(open(f)))
        print(f, json.dumps(out[f], indent=1))
    json.dump(out, open("tab_results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
