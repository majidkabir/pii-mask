# Development

## Layout

```
pii-mask/
├── .claude-plugin/
│   ├── plugin.json        # mod manifest (name, version, state contract)
│   └── marketplace.json   # makes this repo installable with /plugin install
├── hooks/
│   ├── hooks.json         # { "modules": ["./register.tsx"] }
│   ├── register.tsx       # every hook, the model's life cycle, the saved table, the pane
│   └── masker.ts          # pure logic: finds → placeholders, vault, stream unmasker
├── server/
│   ├── setup.py           # one-time install: venv, PyTorch, GLiNER, the model
│   └── server.py          # the shared model server on 127.0.0.1:8766
├── types/index.d.ts       # the $.state contract
├── tests/                 # masker.test.ts, register.test.ts
├── tools/unmask.mjs       # restores real values in claude -p output
└── docs/
```

## Run and test

```bash
claude --plugin-dir .        # load it in a session (hot reloads on save)
claude plugin validate .     # what it hooks and calls; every gating hook has a .catch
claude plugin test .         # all *.test.ts, offline
```

The tests mock everything beneath the mod: the model server (`http.fetch`), Python and the setup script (`process.run`, `process.spawn`), files and the clock.

## The stand-in model

`PII_MASK_FAKE_MODEL=1` makes the mod run `setup.py --fake` (installs nothing) and start `server.py --fake`, which answers with a few fixed rules (emails, `+NN` phone numbers, IBANs and four names: Lars Holm, Mette Sørensen, Peter Jensen, Anna Berg) instead of GLiNER. It exists to test the mod, the server and their life cycle where the model can't be downloaded; never use it for real data.

```bash
PII_MASK_FAKE_MODEL=1 XDG_CACHE_HOME=/tmp/pii-test claude -p --plugin-dir . "…"
```

CI runs the server in fake mode as a smoke test.

## Trying the real server by hand

```bash
python3 server/setup.py --cache ~/.cache/pii-mask
~/.cache/pii-mask/venv/bin/python server/server.py --cache ~/.cache/pii-mask
curl -s -X POST 127.0.0.1:8766/detect \
  -d '{"texts":["Call Anna Berg on +46 70 12 34 56"],"labels":["person","phone number"],"threshold":0.5}'
```

`--idle <seconds>` changes when it stops (default 1800).

## Releasing

Bump `version` in `.claude-plugin/plugin.json` (`claude plugin update` compares it), make sure CI is green, push to `main`.
