# Open questions

Known gaps and what has not been verified yet.

| # | Topic | Status |
| --- | --- | --- |
| 1 | [The real model hasn't run with the mod](#1-the-real-model-hasnt-run-with-the-mod) | 🟡 Setup and model checked in CI on 4 machines; accuracy not measured |
| 2 | [Misses go out as written](#2-misses-go-out-as-written) | 🟡 Patterns added in 0.3; names and addresses still rely on the model |
| 3 | [Speed on big tool results](#3-speed-on-big-tool-results) | ❓ Not measured |
| 4 | [Noise in code](#4-noise-in-code) | ❓ Not measured |
| 5 | [First run blocks the session](#5-first-run-blocks-the-session) | ℹ️ By design; off if the install fails (0.6.0) |
| 6 | [Machines where an organization manages Claude Code](#6-machines-where-an-organization-manages-claude-code) | ⚠️ Partly covered |
| 7 | [Gaps shared with customer-mask](#7-gaps-shared-with-customer-mask) | ⚠️ Known |
| 8 | [Windows](#8-windows) | 🟡 Supported since 0.5.0; checked in CI, not yet on a person's machine |
| 9 | [Intel Macs](#9-intel-macs) | 🟡 Fixed in 0.4.2 (Python 3.11, protobuf), not yet confirmed on one |

### 1. The real model hasn't run with the mod

The machine pii-mask was built on could not reach Hugging Face, so GLiNER was never loaded there. What was verified, with a stand-in model that answers like the server (see [development](development.md)):

- 93 automated tests of the masking, the built-in patterns (with true positives and ordinary text that must stay untouched), every hook and the pane, including first-run setup, starting the server, a slow model and a missing Python.
- Real Claude Code 2.1.302 sessions: on first use the mod ran the setup and started the server by itself; a CSV of names, emails and phone numbers, and a prompt with a name and a phone number, reached the model as placeholders only; files Claude wrote through Bash got the real values.

Since 0.5.0, [real-model.yml](../.github/workflows/real-model.yml) runs the real setup and model on GitHub's machines, the way the mod starts it: install, start detached, one request with a name, an email and a town.

| Machine | Python | Install | Start | One request | Found |
| --- | --- | --- | --- | --- | --- |
| Linux (ubuntu-latest) | 3.12 | 56 s | 6 s | 0.18 s | person, email, address |
| Windows (windows-latest) | 3.12 | 88 s | 12 s | 0.30 s | person, email, address |
| Apple silicon (macos-latest) | 3.12 | 97 s | 31 s | 0.64 s | person, email, address |
| Intel Mac (macos-15-intel) | 3.11 | 148 s | 21 s | 1.54 s | person, email, address |

Install times are on fast data-centre connections; at home the 1.5–2.5 GB download decides. Still to measure: how well it finds personal data across real text, and speed on long tool results ([#3](#3-speed-on-big-tool-results)).

### 2. Misses go out as written

Since 0.3, built-in patterns catch personal data with a fixed shape (emails, phone numbers, card numbers, IBANs, national ID numbers, keys; see [how it works](how-it-works.md#2b-built-in-patterns)), whether the model finds it or not. Names and addresses have no fixed shape, so for those a miss by the model is still sent as written. Once a value has been found, the mod masks it everywhere after that, but the first miss is a leak.

Patterns can also be wrong the other way: a passphrase made only of letters (`correcthorsebattery`) looks like code and is not taken as a secret, phone formats outside the listed ones are left to the model, and an unusual number that happens to look like a phone number is masked (safe, but Claude then sees a placeholder).

### 3. Speed on big tool results

The model takes about 0.8 s per 1,000 characters on a 2-core machine (measured in a cloud container; the GitHub machines in #1 are faster). A call may take 7 seconds; content whose call takes longer is removed, not sent.

What keeps that rare since 0.5.2:
- **Cache.** The server keeps the model's answer per chunk of about 1,000 characters. Claude Code sends the same system prompt and reminders every turn, so after the first turn only new text goes through the model. In a test, hooks that ran out of time went from 4 in the first run to 0 in the third.
- **Small first.** The server runs the model a few chunks at a time, and between those the request with the least left goes next, so a prompt isn't stuck behind a long file.
- **Claude's replies skip the model.** Claude only sees placeholders, so its replies get the patterns and every value found so far, without the model.
- **The note is always sent.** If the system prompt can't be checked in time, its sections are removed but the note about placeholders is still added, so Claude doesn't take placeholders for missing values.

Still to measure on slow machines (an Intel Mac from 2015): long file reads and command output, which can still be removed when the model is slower than the limit.

### 4. Noise in code

NER models tag identifiers in source code as names. The shape checks drop the obvious ones (camelCase, snake_case, code characters), but some ordinary words may still be masked. That is safe, but Claude then sees placeholders where it expects words. A value found once is masked for the rest of the session, so one false find repeats.

### 5. First run blocks the session

Installing takes minutes. A hook can't wait that long, so until the model answers, requests are answered locally and messages are removed. That is the only way to keep "nothing unchecked is sent" without asking for setup.

Since 0.6.0, if the install fails, pii-mask turns itself off instead of holding every request: Claude Code then works as if it weren't installed, and the panel, a toast and the status line say so. It is only on once its model is installed. Once installed, a model that stops working still holds requests, since by then you rely on it.

### 6. Machines where an organization manages Claude Code

There, Claude Code's built-in guard runs before plugins a person installs, and some of their hooks are passed over (`classic.SessionStart`, `classic.UserPromptSubmit`, `prompt.compose`, `prompt.context`, `prompt.attachment`, and in tests also some `session.append` dispatches). In a test on such a machine, prompts and tool results were still masked, but the session title, the system prompt note and the saved table were not. Install it through managed settings there, as [customer-mask's team rollout](https://github.com/majidkabir/customer-mask/blob/main/docs/team-rollout.md) does.

### 7. Gaps shared with customer-mask

- In headless runs (`claude -p`, the SDK), Claude Code titles the session from the raw first prompt before any hook runs, and the printed answer keeps its placeholders: it is taken from the stored reply, which no hook can change for printing. Files written and commands run get the real values as usual.
- `claude --safe-mode` and `--bare` start without any mods.
- Bash gets real values; a command that sends data somewhere itself (`curl` to an outside API) sends them too.
- Masking hides values, not context: a job title, a town or a story can still identify a person.
- Images and PDFs are dropped, not masked.

### 8. Windows

Since 0.5.0. On Windows the mod finds Python through the launcher (`py -3`) first, since `python` there may be the Microsoft Store stub; installs into `%LOCALAPPDATA%\pii-mask` with `venv\Scripts\python.exe`; reads the setup's progress with `\r\n` line ends; and the server detaches by starting itself again as a detached process (no `fork` on Windows), out of the session's job when it may. On Windows the server also refuses to share its port (`SO_REUSEADDR` would let a second one bind it). PyTorch's Windows build needs the Visual C++ runtime; when it's missing, the setup names `winget install Microsoft.VCRedist.2015+.x64` as the fix.

Checked on GitHub's Windows machines: the mod's tests, the setup's tests, a smoke test that starts the server the way the mod does (output captured, so a server holding the caller's pipes would hang it), and [real-model.yml](../.github/workflows/real-model.yml), which installs the real model and checks that it finds a name, on Windows, Linux, Apple silicon and an Intel Mac. Not yet run on a person's Windows machine with Claude Code.

### 9. Intel Macs

PyTorch stopped publishing builds for Intel Macs after 2.2.2. On a 2015 MacBook the first setup failed at "installing PyTorch" (0.3.1 asked for the latest release). 0.3.2 pinned PyTorch 2.2.2, `numpy<2` (2.2.2 was built against numpy 1) and `transformers<4.58` (checked to accept PyTorch 2.2), with Python 3.12 or older; the setup then failed at the model step with "Could not import module 'ModernBertModel'". Reproduced with the same versions: transformers decorates ModernBERT with `torch.compile`, and PyTorch 2.2's compiler refuses Python 3.12 ("Dynamo is not supported on Python 3.12+"). Since 0.4.1 Intel Macs use Python 3.11 or older. With Python 3.11, PyTorch 2.2.2, numpy 1.26.4, transformers 4.57.6 and GLiNER 0.2.29, GLiNER imports and a DeBERTa-v2 network (what the PII model is built on) runs; the real model's weights could not be downloaded where this was tested. An environment built earlier with a newer Python is replaced. With 0.4.1 the model step then failed with an `ImportError`: loading the model converts its mdeberta tokenizer, which needs `protobuf`, and GLiNER doesn't depend on it. Reproduced by building a DeBERTa-v2 fast tokenizer in the same environment; since 0.4.2 the setup installs `protobuf` and `sentencepiece` on every platform, and shows the message transformers puts on the line after `ImportError:`. The model will also be slower on those machines.

Other machines (Linux, Apple silicon) take the latest PyTorch and transformers 5. Checked on Linux with Python 3.11, 3.13 and 3.14 (PyTorch 2.14, transformers 5.16, GLiNER 0.2.29): a small GLiNER model with the same tokenizer as the PII model is saved, loaded offline with `from_pretrained` and runs `predict_entities`. Since 0.4.3 transformers is held below 6.
