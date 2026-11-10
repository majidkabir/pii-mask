# pii-mask

**Use Claude Code without sending your personal data to the cloud.**

<img src="docs/assets/flow.gif" alt="A message with a name, an email and a phone number passes pii-mask on its way to Claude and turns into placeholders; a web search gets placeholders too; Claude's answer and the file it writes come back with the real values." width="100%">

When you work with Claude Code, everything it reads goes to Claude: your messages, your files, the output of your commands. If those contain names, email addresses, phone numbers or account numbers, they go along too.

pii-mask stops that. Before anything leaves your computer, it swaps personal data for harmless placeholders. Claude works with the placeholders, and you still see the real values in every answer and in every file Claude writes for you.

## What it looks like

You write:

> Send a reminder to **Anna Berg** at **anna@berg.se** about the invoice.

Claude receives:

> Send a reminder to **‹PERSON 1›** at **‹EMAIL 1›** about the invoice.

Claude answers *"I've written the reminder to ‹PERSON 1›"*, and you read **"I've written the reminder to Anna Berg"**. The email Claude drafts into a file has Anna's real name and address in it. Claude never saw them.

## What it hides

Names · email addresses · phone numbers · home addresses · bank accounts and IBANs · card numbers · personal ID numbers (US, UK, Denmark, Sweden, Norway, Finland, and any number labelled as an ID) · passport numbers · dates of birth · IP and device addresses · GPS coordinates · passwords and API keys

You don't make a list or set anything up. Two things work together, both **on your own computer**:

- **A small AI model** recognises personal data in any text, in many languages, including what has no fixed form, like names and addresses.
- **A built-in checklist of about 30 patterns** catches everything with a fixed shape: email addresses, phone numbers, card numbers, IBANs, ID numbers, keys. Where a number has a check digit, the checklist verifies it, so ordinary numbers in your work aren't hidden by mistake. It also catches those when the model misses them.

## Images and PDFs

- **A PDF with text** is sent as its text, with the personal data masked like any text.
- **An image Claude reads from a file, or you mention with `@`** (a screenshot, a scanned page, a business card) is read on your computer. Claude gets a copy with **black boxes over the personal data and faces pixelated**, plus the masked text in it.
- **A scanned PDF** is rebuilt from its pages, covered the same way (the first 2 pages).
- **An image pasted into your message** can't be swapped (Claude Code doesn't let plugins change those), so it is left out and its masked text is sent instead. To send it covered, save it as a file and ask Claude to read it.

The copies Claude gets are kept in `.claude/pii-mask` in your project (the last 20, ignored by git), so you can see exactly what was sent. Each part can be switched off:

```
/pii-mask media                  show what is on
/pii-mask media images off       pdf · images · faces · image-text
```

## Get started

You need Claude Code on a Mac, Linux or Windows computer, and Python 3.9 or newer. Most Macs and Linux machines already have it; on Windows, install it with `winget install Python.Python.3.12` or from python.org. On an older Mac with an Intel processor, it also needs Python 3.11 or older next to it (`brew install python@3.11`), because the AI library it uses has no newer version for those Macs.

**1. Install it.** In Claude Code, type:

```
/plugin install pii-mask --marketplace majidkabir/pii-mask
```

**2. Wait once.** The first time, pii-mask downloads its model (about 1.5–2.5 GB) and sets it up. That takes a few minutes. A panel above where you type shows how it's going:

```
⏳ pii-mask is installing its local model (first time only, about 1.5–2.5 GB)
█████████░░░░░░░░░░░░░░░  step 2 of 4: installing PyTorch (the largest download)
412 MB so far · 1 min 40 s on this step · Nothing is sent to Claude until it's ready.
```

When it's done, a message says **pii-mask is on** and the panel goes away. Nothing is sent to Claude while it installs.

If the install fails, nothing is sent to Claude. The panel says why, gives you the exact command that fixes it, and has a **Retry** button. To work without masking until it's fixed, type `/pii-mask off`: that lasts for the current session only.

**3. Work as usual.** From then on, it starts by itself whenever you open Claude Code.

## See what was hidden

Type `/pii-mask` to open a side pane that lists everything hidden in this session, grouped by kind:

```
🔒 pii-mask on
3 values masked. Claude sees the placeholder; the real value stays on this machine.
People (1)
  Anna Berg           <<PERSON_1.k3f9>>
Emails (1)
  anna@berg.se        <<EMAIL_1.k3f9>>
Phone numbers (1)
  +46 70 12 34 56     <<PHONE_1.k3f9>>
```

The pane updates while you work. It's only shown on your screen and is never sent to Claude.

## Good to know

- **When in doubt, it holds back.** If the model isn't ready or can't check something in time, that content is left out rather than sent. Claude tells you, and you can try again.
- **Web searches and network commands stay private too.** When Claude searches the web, uses a connected service, or runs a command that sends data over the network (`curl`, `ssh`, `gh`, `git push`), it sends the placeholder, not the real value. Files Claude writes for you hold the real values, so pushing or uploading them sends what's in them.
- **It's not perfect.** A name the model doesn't recognise goes through as written. And the rest of a sentence can still give someone away ("my sister, the only dentist in Skagen").
- **Images: what can be read is covered.** pii-mask covers the text it can read in an image and the faces it finds. Handwriting, a signature, very small or stylised text, or a face it misses can stay visible. Reading an image takes 5–30 seconds ([performance](docs/performance.md)); one that can't be read in time is left out, never sent as it was. OCR misreads, so long numbers, ID numbers, an ID card's machine-readable lines and anything with an `@` are covered whether or not they look like personal data. Dates stay visible (a receipt's date), unless the image mentions a date of birth: then every date in it is covered.
- **Everything stays on your computer.** The model runs locally and stops by itself when you're not using it. Only Claude Code under your user account can use it: it needs a key only you can read, and it refuses web pages. If two people use pii-mask on one computer, give one of them another port with `PII_MASK_PORT=8767`. To remove it completely, delete the folder `~/.cache/pii-mask` (on Windows: `%LOCALAPPDATA%\pii-mask`).

## Status

Young, and tested: every week on Linux, Windows and Macs (Apple and Intel) with the real model, on two public benchmarks ([results](docs/benchmark.md)), and in real Claude Code sessions. It hides most personal data, not all of it: `/pii-mask` shows what it hid.

## More

- [How it works](docs/how-it-works.md): the technical details
- [Benchmark](docs/benchmark.md): accuracy on two public benchmarks, next to Microsoft Presidio
- [Performance](docs/performance.md): how fast it is on 4 kinds of machine, and what it costs
- [Open questions](docs/open-questions.md): known gaps
- [Development](docs/development.md): building and testing it
- [customer-mask](https://github.com/majidkabir/customer-mask): the sibling project, for hiding a company's customers from a list

MIT licence. The model it downloads, [GLiNER PII](https://huggingface.co/urchade/gliner_multi_pii-v1), is Apache-2.0.
