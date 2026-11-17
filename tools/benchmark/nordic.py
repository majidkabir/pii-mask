"""A small check in Nordic and other European languages, which the public
benchmarks (English) don't cover: invented sentences with the personal data in
each marked, sent to the model server's /detect with the mod's labels. Prints
recall and precision of the model's own finds (no patterns), by kind.

  python nordic.py --cache <server cache> [--port 8766]
"""
import argparse
import json
import os
import urllib.request

AT = "@"
# (language, sentence, [personal data in it])
CASES = [
    ("da", f"Hej Mette Sørensen, din ordre er sendt til Vesterbrogade 112, 1620 København V. Spørgsmål? Skriv til mette.sorensen{AT}gmail.com.", ["Mette Sørensen", "Vesterbrogade 112, 1620 København V", f"mette.sorensen{AT}gmail.com"]),
    ("da", "Kontaktperson er Jens Peter Nielsen, telefon 28 47 19 63, og han svarer hverdage.", ["Jens Peter Nielsen", "28 47 19 63"]),
    ("da", "Lønnen udbetales til konto DK50 0040 0440 1162 43 i Danske Bank for Camilla Bach.", ["DK50 0040 0440 1162 43", "Camilla Bach"]),
    ("da", "Patienten Søren Kragh, født 14.03.1985, bor på Nørregade 7, 8000 Aarhus C.", ["Søren Kragh", "14.03.1985", "Nørregade 7, 8000 Aarhus C"]),
    ("da", "Mødet med advokat Line Holm er flyttet. Ring på +45 40 11 22 33.", ["Line Holm", "+45 40 11 22 33"]),
    ("sv", f"Hej Maja Lindholm, vi har tagit emot din ansökan. Svara till maja.lindholm{AT}posten.se.", ["Maja Lindholm", f"maja.lindholm{AT}posten.se"]),
    ("sv", "Leveransadress: Drottninggatan 53, 111 21 Stockholm, mottagare Erik Johansson.", ["Drottninggatan 53, 111 21 Stockholm", "Erik Johansson"]),
    ("sv", "Ring Anna-Karin Berg på 070-123 45 67 om fakturan.", ["Anna-Karin Berg", "070-123 45 67"]),
    ("no", f"Kari Nordmann flytter til Storgata 15, 0155 Oslo. Ny e-post: kari.nordmann{AT}online.no.", ["Kari Nordmann", "Storgata 15, 0155 Oslo", f"kari.nordmann{AT}online.no"]),
    ("no", "Beløpet går til kontonummer NO93 8601 1117 947, eier Ola Hansen.", ["NO93 8601 1117 947", "Ola Hansen"]),
    ("no", "Lege Ingrid Bakke kan nås på +47 912 34 567.", ["Ingrid Bakke", "+47 912 34 567"]),
    ("fi", "Asiakas Mikko Virtanen, puhelin 040 123 4567, osoite Mannerheimintie 12, 00100 Helsinki.", ["Mikko Virtanen", "040 123 4567", "Mannerheimintie 12, 00100 Helsinki"]),
    ("de", f"Sehr geehrte Frau Petra Schmidt, bitte melden Sie sich unter petra.schmidt{AT}web.de oder 030 1234567.", ["Petra Schmidt", f"petra.schmidt{AT}web.de", "030 1234567"]),
    ("de", "Die Rechnung geht an Thomas Müller, Brucker Bundesstraße 31, 82205 Gilching.", ["Thomas Müller", "Brucker Bundesstraße 31, 82205 Gilching"]),
    ("nl", f"Beste Sanne de Vries, uw pakket wordt bezorgd op Keizersgracht 221, 1016 DV Amsterdam. Vragen: sanne.devries{AT}ziggo.nl.", ["Sanne de Vries", "Keizersgracht 221, 1016 DV Amsterdam", f"sanne.devries{AT}ziggo.nl"]),
    ("en", f"Our contact at the client is Henrik Bach, henrik.bach{AT}bachbyg.dk, +45 31 22 48 90.", ["Henrik Bach", f"henrik.bach{AT}bachbyg.dk", "+45 31 22 48 90"]),
    ("en", "Refund to IBAN DE89 3704 0044 0532 0130 00, account holder Agnete Holst, passport no. 209487523.", ["DE89 3704 0044 0532 0130 00", "Agnete Holst", "209487523"]),
    ("en", "The log shows a login from 83.94.120.17 by user Troels Munk.", ["83.94.120.17", "Troels Munk"]),
    # Nothing personal: what a developer's text is full of.
    ("en", "The OrderService calls PaymentGateway.charge() and retries three times on timeout.", []),
    ("da", "Ret fejlen i faktura-modulet og kør testene igen før fredag.", []),
    ("en", "Kafka topic shipments.v2 has 12 partitions; consumer group co2-calc lags by 340 messages.", []),
]
LABELS = ["person", "email", "phone number", "address", "iban", "bank account number", "credit card number",
          "social security number", "national id number", "passport number", "date of birth", "ip address"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", required=True)
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--json")
    a = p.parse_args()
    key = open(os.path.join(a.cache, "token"), encoding="utf-8").read().strip()
    body = json.dumps({"texts": [c[1] for c in CASES], "labels": LABELS, "threshold": 0.5}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}/detect", body,
                                 {"content-type": "application/json", "x-pii-mask-key": key})
    results = json.loads(urllib.request.urlopen(req, timeout=600).read())["results"]
    hit = gold = kept = finds = 0
    by_lang, misses, wrong = {}, [], []
    for (lang, text, want), found in zip(CASES, results):
        spans = [(f["start"], f["end"]) for f in found]
        covered = lambda v: any(s <= text.index(v) and text.index(v) + len(v) <= e or
                                (s < text.index(v) + len(v) and e > text.index(v) and (min(e, text.index(v) + len(v)) - max(s, text.index(v))) >= 0.7 * len(v))
                                for s, e in spans)
        h = [v for v in want if covered(v)]
        hit += len(h)
        gold += len(want)
        misses += [f"{lang}: {v}" for v in want if v not in h]
        right = [f for f in found if any(text.index(v) < f["end"] and f["start"] < text.index(v) + len(v) for v in want)]
        kept += len(right)
        finds += len(found)
        wrong += [f"{lang}: {f['text']} ({f['label']})" for f in found if f not in right]
        l = by_lang.setdefault(lang, [0, 0])
        l[0] += len(h)
        l[1] += len(want)
    out = {"recall": round(hit / gold, 3), "precision": round(kept / max(1, finds), 3),
           "by_language": {k: f"{v[0]}/{v[1]}" for k, v in by_lang.items()}, "missed": misses, "not_personal": wrong}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.json:
        json.dump(out, open(a.json, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
