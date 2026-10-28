# Open questions

Known gaps and what has not been verified yet.

| # | Topic | Status |
| --- | --- | --- |
| 1 | [The real model hasn't run with the mod](#1-the-real-model-hasnt-run-with-the-mod) | ⚠️ Not verified |
| 2 | [Misses go out as written](#2-misses-go-out-as-written) | 🟡 Patterns added in 0.3; names and addresses still rely on the model |
| 3 | [Speed on big tool results](#3-speed-on-big-tool-results) | ❓ Not measured |
| 4 | [Noise in code](#4-noise-in-code) | ❓ Not measured |
| 5 | [First run blocks the session](#5-first-run-blocks-the-session) | ℹ️ By design |
| 6 | [Machines where an organization manages Claude Code](#6-machines-where-an-organization-manages-claude-code) | ⚠️ Partly covered |
| 7 | [Gaps shared with customer-mask](#7-gaps-shared-with-customer-mask) | ⚠️ Known |
| 8 | [Windows](#8-windows) | ❌ Not supported |
| 9 | [Intel Macs](#9-intel-macs) | 🟡 Fixed in 0.4.1 (Python 3.11), not yet confirmed on one |

### 1. The real model hasn't run with the mod

The machine pii-mask was built on could not reach Hugging Face, so GLiNER was never loaded there. What was verified, with a stand-in model that answers like the server (see [development](development.md)):

- 93 automated tests of the masking, the built-in patterns (with true positives and ordinary text that must stay untouched), every hook and the pane, including first-run setup, starting the server, a slow model and a missing Python.
- Real Claude Code 2.1.302 sessions: on first use the mod ran the setup and started the server by itself; a CSV of names, emails and phone numbers, and a prompt with a name and a phone number, reached the model as placeholders only; files Claude wrote through Bash got the real values.

Still to verify on a real machine: that `setup.py` installs PyTorch and GLiNER and downloads the model, that the model loads within the 3-minute limit, and how well it finds what it should.

### 2. Misses go out as written

Since 0.3, built-in patterns catch personal data with a fixed shape (emails, phone numbers, card numbers, IBANs, national ID numbers, keys; see [how it works](how-it-works.md#2b-built-in-patterns)), whether the model finds it or not. Names and addresses have no fixed shape, so for those a miss by the model is still sent as written. Once a value has been found, the mod masks it everywhere after that, but the first miss is a leak.

Patterns can also be wrong the other way: a passphrase made only of letters (`correcthorsebattery`) looks like code and is not taken as a secret, phone formats outside the listed ones are left to the model, and an unusual number that happens to look like a phone number is masked (safe, but Claude then sees a placeholder).

### 3. Speed on big tool results

A model call may take 7 seconds; a row whose call takes longer is removed. GLiNER on a CPU is much slower than spaCy, so large file reads and long command output may often be removed rather than masked. Measure on a real machine: per prompt, per 10 KB and per 100 KB.

### 4. Noise in code

NER models tag identifiers in source code as names. The shape checks drop the obvious ones (camelCase, snake_case, code characters), but some ordinary words may still be masked. That is safe, but Claude then sees placeholders where it expects words. A value found once is masked for the rest of the session, so one false find repeats.

### 5. First run blocks the session

Installing takes minutes. A hook can't wait that long, so until the model answers, requests are answered locally and messages are removed. That is the only way to keep "nothing unchecked is sent" without asking for setup.

### 6. Machines where an organization manages Claude Code

There, Claude Code's built-in guard runs before plugins a person installs, and some of their hooks are passed over (`classic.SessionStart`, `classic.UserPromptSubmit`, `prompt.compose`, `prompt.context`, `prompt.attachment`, and in tests also some `session.append` dispatches). In a test on such a machine, prompts and tool results were still masked, but the session title, the system prompt note and the saved table were not. Install it through managed settings there, as [customer-mask's team rollout](https://github.com/majidkabir/customer-mask/blob/main/docs/team-rollout.md) does.

### 7. Gaps shared with customer-mask

- In headless runs (`claude -p`, the SDK), Claude Code titles the session from the raw first prompt before any hook runs.
- `claude --safe-mode` and `--bare` start without any mods.
- Bash gets real values; a command that sends data somewhere itself (`curl` to an outside API) sends them too.
- Masking hides values, not context: a job title, a town or a story can still identify a person.
- Images and PDFs are dropped, not masked.

### 8. Windows

The server detaches with `fork`, and the setup expects `venv/bin/python`. Both are macOS and Linux only; on Windows the mod blocks with a message.

### 9. Intel Macs

PyTorch stopped publishing builds for Intel Macs after 2.2.2. On a 2015 MacBook the first setup failed at "installing PyTorch" (0.3.1 asked for the latest release). 0.3.2 pinned PyTorch 2.2.2, `numpy<2` (2.2.2 was built against numpy 1) and `transformers<4.58` (checked to accept PyTorch 2.2), with Python 3.12 or older; the setup then failed at the model step with "Could not import module 'ModernBertModel'". Reproduced with the same versions: transformers decorates ModernBERT with `torch.compile`, and PyTorch 2.2's compiler refuses Python 3.12 ("Dynamo is not supported on Python 3.12+"). Since 0.4.1 Intel Macs use Python 3.11 or older. With Python 3.11, PyTorch 2.2.2, numpy 1.26.4, transformers 4.57.6 and GLiNER 0.2.29, GLiNER imports and a DeBERTa-v2 network (what the PII model is built on) runs; the real model's weights could not be downloaded where this was tested. An environment built earlier with a newer Python is replaced. The model will also be slower on those machines.
