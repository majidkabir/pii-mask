# Benchmark scripts

Runs pii-mask's masking on two public benchmarks and scores it next to Microsoft Presidio. Results: [docs/benchmark.md](../../docs/benchmark.md).

`run.ts` runs the mod's own code (`hooks/masker.ts`, `hooks/patterns.ts`) against the local model server, the way the mod masks a file: the model with the mod's labels and threshold, its shape checks, then values already found, the built-in patterns and the model's finds. It starts a fresh vault per document and reads long documents in pieces of about 8,000 characters.

```bash
# 1. The data (both on GitHub)
git clone --depth 1 https://github.com/NorskRegnesentral/text-anonymization-benchmark data/tab
git clone --depth 1 https://github.com/microsoft/presidio-research data/presidio-research

# 2. A Python environment for the scorers and Presidio
python3 -m venv venv && venv/bin/pip install presidio-analyzer presidio-evaluator spacy intervaltree
venv/bin/pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl
venv/bin/pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl
venv/bin/pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

# 3. The documents as {"id","text"} lines
python3 -c "import json; [print(json.dumps({'id': d['doc_id'], 'text': d['text']})) for d in json.load(open('data/tab/echr_test.json'))]" > tab_in.jsonl
python3 -c "import json; [print(json.dumps({'id': str(i), 'text': r['full_text']})) for i, r in enumerate(json.load(open('data/presidio-research/data/synth_dataset_v2.json')))]" > synth_in.jsonl

# 4. pii-mask (its model server must be running on 127.0.0.1:8766, as after a session with the mod)
bun run.ts tab_in.jsonl tab_piimask.jsonl
bun run.ts synth_in.jsonl synth_piimask.jsonl
python3 -c "import json; print(json.dumps({r['id']: [[s, e] for s, e, _ in r['spans']] for r in map(json.loads, open('tab_piimask.jsonl'))}))" > tab_piimask.json

# 5. Presidio on TAB, then the scores
venv/bin/python presidio_tab.py data/tab/echr_test.json tab_presidio.json
venv/bin/python tab_score.py data/tab/echr_test.json tab_presidio.json tab_piimask.json
venv/bin/python eval_presidio.py synth_piimask.jsonl data/presidio-research/data/synth_dataset_v2.json
```

`tab_score.py` computes TAB's measures as its `evaluation.py` defines them, without the BERT weighting (which needs `bert-base-uncased`). Run on Presidio's output it gives numbers close to the TAB paper's Presidio row, which checks it. `eval_presidio.py` uses presidio-evaluator's own `SpanEvaluator` as its notebook 4 does.
