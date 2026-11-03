# Benchmark

pii-mask measured on two public, standard benchmarks, next to Microsoft Presidio scored the same way on the same machine. Measured November 2026 with pii-mask 0.7.7 (model `urchade/gliner_multi_pii-v1`), Presidio 2.2.366 with `en_core_web_lg`. Scripts and steps to rerun: [tools/benchmark](../tools/benchmark/README.md).

**Short version:** pii-mask finds people, card numbers, phone numbers and street addresses as well as or better than Presidio, and masks less that shouldn't be masked. It finds less in total because it leaves out what isn't personal data in a developer's work by design: dates, countries, nationalities, ages, organisations, domains and case numbers.

## 1. TAB: Text Anonymization Benchmark

[TAB](https://github.com/NorskRegnesentral/text-anonymization-benchmark) (Pilán et al., *Computational Linguistics* 2022): 127 test documents, 648,000 characters of European Court of Human Rights rulings. Annotators marked every mention that identifies the applicant: **direct** identifiers (names, case numbers) and **quasi** identifiers (dates, places, organisations, nationalities, amounts). An entity counts as masked only if *every* mention of it is masked.

| System | Entity recall, direct | Entity recall, quasi | Mention recall | Mention precision | Token recall, people |
| --- | --- | --- | --- | --- | --- |
| **pii-mask 0.7.7** | 0.474 | 0.210 | 0.242 | **0.850** | **0.948** |
| pii-mask 0.7.6 | 0.466 | 0.204 | 0.238 | 0.476 | 0.938 |
| Presidio, measured here | 0.486 | 0.764 | 0.716 | 0.715 | 0.911 |
| *Published (TAB paper, test set):* | | | | | |
| *Presidio (default)* | *0.460* | *0.758* | *0.707* | *0.761* | |
| *RoBERTa NER (OntoNotes)* | *0.940* | *0.874* | *0.906* | *0.441* | |
| *Longformer trained on TAB* | *1.000* | *0.916* | *0.919* | *0.836* | |

How to read it:
- **Direct identifiers** are half names and half case numbers ("no. 36244/06"). On names alone, entity recall is 0.943 for pii-mask and 0.957 for Presidio; on case numbers 0.000 and 0.012. A case number isn't personal data outside a court ruling, so pii-mask doesn't look for it.
- **Quasi identifiers** are mostly dates, places and organisations. pii-mask leaves those out by design, so its total recall is low on TAB. That is the main reason the published systems built for court rulings score higher.
- **Precision**: of what pii-mask masks, 85% is something the annotators masked too, more than Presidio (72%) and the RoBERTa NER (44%).
- Presidio measured here comes within 0.03 of the published Presidio row on recall (precision is 0.05 lower, a newer Presidio masks more). That checks the scorer, written from TAB's `evaluation.py` (its BERT weighting left out).

## 2. Presidio's evaluation set

[synth_dataset_v2.json](https://github.com/microsoft/presidio-research/tree/main/data) from Microsoft's presidio-research: 1,500 synthetic sentences with 2,863 labelled spans. Scored with Microsoft's own `presidio-evaluator` (0.3.2) as its notebook does: spans match at 75% overlap, PII vs not PII, F2 (recall counts twice as much as precision).

| System | Precision | Recall | F2 |
| --- | --- | --- | --- |
| **pii-mask 0.7.7** | **0.808** | 0.544 | 0.582 |
| pii-mask 0.7.6 | 0.764 | 0.503 | 0.540 |
| Presidio, measured here | 0.680 | 0.575 | 0.593 |
| *Presidio, published in presidio-research's notebook* | *0.733* | *0.646* | *0.661* |

The published Presidio row couldn't be reproduced here (another Presidio or evaluator version may explain it), so compare the two rows measured here.

Share of each kind of value hidden (characters inside a masked span):

| Kind | Spans | pii-mask | Presidio |
| --- | --- | --- | --- |
| Person | 857 | **0.90** | 0.86 |
| Street address | 598 | **0.58** | 0.27 |
| Card number | 136 | **0.97** | 0.86 |
| Phone number | 92 | **0.78** | 0.69 |
| Email, IBAN, IP address, US SSN | 100 | 1.00 | 1.00 |
| ZIP code | 37 | **0.30** | 0.06 |
| Country or city (GPE) | 411 | 0.13 | 0.58 |
| Organisation | 250 | 0.11 | 0.23 |
| Date or time | 119 | 0.29 | 0.83 |
| Age | 74 | 0.00 | 0.82 |
| Nationality, religion, politics (NRP) | 55 | 0.02 | 0.66 |
| Domain name | 37 | 0.00 | 1.00 |
| Job title | 92 | 0.01 | 0.06 |
| US driver's licence | 5 | 0.00 | 1.00 |

## 3. What the benchmarks changed (0.7.7)

- **Roles were taken for names.** The model tagged "The applicant", "Secretary of State", "His wife" and "the prison governor" as people, and a value found once is masked everywhere, so "the applicant" became a placeholder 1,106 times in TAB. Role words are now never a name on their own, nor is anything starting with an article or a possessive. TAB precision went from 0.476 to 0.850.
- **"Mr Holm" after "Lars Holm" was missed.** The surname of a name already found is now masked right after a title (Mr, Mrs, Dr, Herr, Frau...), with its capital, and nowhere else.
- **Addresses over several lines were dropped.** The model found them, but the shape check refused a line break. Street addresses hidden on Presidio's set went from 0.29 to 0.58.

## 4. Limits

- Both sets are English. pii-mask's model is multilingual, and the patterns cover Nordic and EU formats; neither set tests that.
- Benchmarks that need Hugging Face (ai4privacy, Nemotron-PII, Gretel finance, used in recent comparisons) couldn't be downloaded where this was run.
- TAB's mentions starting with "Dr" count as missed when pii-mask masks only the name, since its scorer ignores "Mr" and "Mrs" but not "Dr".
- One run, CPU, no confidence intervals.
