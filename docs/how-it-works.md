# How it works

pii-mask is built like [customer-mask](https://github.com/majidkabir/customer-mask), with one difference: a local NER model decides what to mask, not a list.

## 1. The hooks

All hooks are in [`hooks/register.tsx`](../hooks/register.tsx). The masking itself is a pure module, [`hooks/masker.ts`](../hooks/masker.ts), with no Claude Code dependency.

| Event | What it does |
| --- | --- |
| `session.append` | Every row the conversation keeps (prompts, tool results, replies) passes here before it is stored and before any request sends it. The row's text goes to the model, what it finds is masked, and images and PDFs are removed. If the model can't check the row, a `.catch` stores it with its text replaced by a note. |
| `prompt.compose` | Masks the per-session parts of the system prompt and adds a note telling Claude how to use placeholders. Runs for every request; the same text gives the same output, so the prompt cache keeps working. |
| `prompt.context` | Masks `CLAUDE.md` and the other blocks of the first message. If the model wasn't running yet, they are replaced by notes and rendered again once it is. |
| `prompt.attachment` | Masks text Claude Code injects (an `@file` mention, reminders); left out if that fails. |
| `classic.UserPromptSubmit` | Titles the session from the masked prompt, so the raw prompt isn't sent to make a title. |
| `turn.step` | Unmasks the reply as it streams, holding back a placeholder split across two pieces. While the model isn't running, answers locally with the reason instead of calling Claude. |
| `ui.render` (AssistantMessage, UserMessage, ToolUse) | Shows real values in the finished reply, the prompt and a tool call's row. Claude Code draws those from the stored rows, which hold placeholders; only the drawing changes. `WebSearch`, `WebFetch` and MCP rows keep the placeholders they sent. |
| `tool.call` | Restores real values in tool arguments before the permission prompt and the tool see them. `WebSearch`, `WebFetch` and every MCP tool keep their placeholders. Denies the call if restoring fails. |
| `classic.SessionStart` | Finds the transcript; on resume, adopts the placeholder table saved beside it. |
| `session.end` | Masks the copies Claude Code wrote to disk on its own, with every value found during the session. |
| `session.start`, `command.run` | Status line and `/pii-mask`. |
| `ui.render` (Pane) | Draws the pane `/pii-mask` opens: every masked value by kind with its placeholder, and the model's state. It reads both from the session's state, so it redraws by itself when a value is masked. It is drawn on this machine only. |

## 2. Finding the values

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant Mod as pii-mask
    participant NER as GLiNER (127.0.0.1:8766)
    participant API as Anthropic API
    CC->>Mod: row: "Call Anna Berg on +46 70 12 34 56"
    Mod->>NER: POST /detect (text, 12 labels, threshold 0.5)
    NER-->>Mod: person "Anna Berg", phone number "+46 70 12 34 56"
    Note over Mod: drop finds that can't be what they claim,<br/>add every value found earlier this session
    Mod-->>CC: "Call <<PERSON_1>> on <<PHONE_1>>"
    CC->>API: placeholders only
```

- **One request per row.** All of a row's text goes to the server in one call. Text it already answered for (the system prompt, sent with every request) comes from a cache.
- **Long text is split** into chunks of whole lines, up to 1,000 characters each, because GLiNER reads about 384 words at a time. The line numbers Claude Code's Read tool adds are skipped, and positions are mapped back to the original text.
- **A value found once stays masked.** Every value in the placeholder table is matched again in all later text, in any case and with any spacing, so a name the model recognised in one place is masked where it misses it in another.
- **Mistakes are dropped.** Each label has a shape check: a person's name has no code characters or camelCase, an email has an `@`, a phone number at least 6 digits, a card number at least 12. Claude Code's own words (`Claude`, `Bash`, `JSON`, …) and the placeholder labels are never masked.

## 2b. Built-in patterns

Before the model's finds, every text passes [`hooks/patterns.ts`](../hooks/patterns.ts): patterns for personal data with a fixed shape. They catch what the model misses there and make those kinds exact. Where the data has a checksum, a match must pass it; context words are required where the shape alone is too common. They run in this order, and earlier ones win:

| Kind | Placeholder | What counts |
| --- | --- | --- |
| Private keys | `SECRET` | a PEM `PRIVATE KEY` block |
| Password in a URL | `SECRET` | the password in `scheme://user:password@host` |
| API keys and tokens | `SECRET` | AWS, GitHub, Slack, Stripe, Google, Anthropic/OpenAI keys, JSON web tokens |
| Password or key in a setting | `SECRET` | the value after `password`, `secret`, `api_key`, `token`, … `=` or `:`, unless it is code (`getPassword()`, `${VAR}`, `os.environ[…]`) |
| Email | `EMAIL` | any address |
| IBAN | `IBAN` | mod-97 check passes |
| Card number | `CARD` | 13–19 digits, a card network's prefix, Luhn check passes |
| Date of birth | `BIRTHDATE` | a date after "date of birth", "DOB", "born", "fødselsdato", "født", "född", "geboren", … |
| Passport number | `PASSPORT` | 6–9 letters and digits after "passport", "pasnummer", "Reisepass", … |
| Account number | `ACCOUNT` | digits after "account", "konto", "reg. nr.", … |
| ID named as one | `ID_NUMBER` | a number after "SSN", "national ID", "tax ID", "CPR", "personnummer", "BSN", "DNI", "PESEL", … |
| US Social Security number | `ID_NUMBER` | `NNN-NN-NNNN`, no 000/666/9xx area, 00 group or 0000 serial |
| Danish CPR | `ID_NUMBER` | `DDMMYY-NNNN` with a real date |
| Swedish personnummer | `ID_NUMBER` | `(YY)YYMMDD-NNNN`, Luhn check |
| Norwegian fødselsnummer | `ID_NUMBER` | 11 digits, both mod-11 checks |
| Finnish henkilötunnus | `ID_NUMBER` | `DDMMYY-NNNC`, check character |
| UK National Insurance | `ID_NUMBER` | `AB 12 34 56 C`, valid prefixes |
| MAC address | `MAC` | six hex pairs |
| IPv6, IPv4 | `IP` | valid addresses; not `127.x`, `0.0.0.0`, `::1`, or a version number (`v1.2.3.4`, `==1.2.3.4`) |
| Coordinates | `LOCATION` | a latitude and longitude with 4+ decimals |
| Phone number | `PHONE` | `+` or `00` and a country code, 8–15 digits; or common national forms (`(555) 123-4567`, `020 7946 0958`, `07700 900123`, `12 34 56 78`) |

Checked against 280 KB of real source code and documentation (this repository and customer-mask), the patterns flagged only the example personal data those files contain. A test also runs ordinary technical text through them (commit hashes, UUIDs, timestamps, version numbers, ports, prices, colours, times) and expects nothing to be found. One MB of mixed text takes under a second.

## 3. The model's life cycle

```mermaid
flowchart TD
    S([A session starts]) --> H{Server answers<br/>on 127.0.0.1:8766?}
    H -- yes --> R[Ready]
    H -- no --> I{Installed?<br/>~/.cache/pii-mask/installed.json}
    I -- no --> P{Python 3.9+?}
    P -- no --> F[["Blocked: install Python"]]
    P -- yes --> U["server/setup.py, in the background:<br/>venv · PyTorch · GLiNER · model"]
    U -- fails --> F2[["Blocked: see setup.log,<br/>then /pii-mask retry"]]
    U -- done --> D
    I -- yes --> D["server/server.py --daemon<br/>(detached, shared by all sessions)"]
    D --> W{Answers within 3 min?}
    W -- yes --> R
    W -- no --> F3[["Blocked: see server.log"]]
    R --> X{"30 min without requests"}
    X --> Z[Server stops; the next call starts it again]

    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef bad fill:#ffebee,stroke:#c62828,color:#b71c1c
    class R ok
    class F,F2,F3 bad
```

- **What you see**: while the model isn't ready, a panel above the prompt (`ui.render` on `AbovePrompt`) shows the install step with a progress bar, the MB downloaded so far (the install folder's size, reported every 3 s; downloads go through a temporary folder inside it) and the time on the step, or, when starting, the time it takes. On a failure it shows the reason, a fix when one is known (`FIX` lines from the setup, or the mod's own: how to install Python), and Retry and Details buttons. A toast says when it fails and when it is ready. The pane shows the same. pii-mask's own messages (its answer when it stops a request, `/pii-mask status`) skip the model check, so they are never removed while the model is down.
- **Install** (`server/setup.py`): a virtual environment in `~/.cache/pii-mask/venv` (`$XDG_CACHE_HOME/pii-mask` when set; on Windows `%LOCALAPPDATA%\pii-mask`, found through `py -3`), PyTorch (on Linux from the CPU-only index first, which is a fraction of the default build; on Intel Macs 2.2.2, the last release built for them, with `numpy<2` and `transformers<4.58`, and a Python of 3.11 or older found on the PATH, since PyTorch 2.2's compiler does not run on 3.12), `gliner`, and the model, downloaded to `~/.cache/pii-mask/hf`. It reports each step to the status line and logs to `setup.log`. A hook can't run for minutes, so this runs in the background while requests are answered locally with the progress.
- **Run** (`server/server.py`): detaches from the session that started it, so it keeps serving the other sessions. It binds `127.0.0.1` only, refuses a second start on the same port, loads the model with Hugging Face set to offline (nothing is fetched at run time), and never logs request text.
- **Wait**: a hook that needs the model while it is loading waits up to 6 seconds, then fails closed. A model call may take 7 seconds. A server that stopped while idle is started again on the next call.

## 4. Placeholders

`<<LABEL_n.mmmm>>`, for example `<<PERSON_1.k3f9>>`:

- **`LABEL_n`**: the same value gets the same number for the whole session, so Claude can reason across turns and earlier messages never change.
- **`-k`** after the number (`PERSON_1-2`): another spelling of the same value (`ANNA BERG`). Each spelling is restored exactly as written, which Claude's Edit tool needs to find the text in a file.
- **`.mmmm`**: a random marker chosen when the session starts. Only placeholders with this marker are restored, so text that just looks like a placeholder is left alone.
- **The table** lives in memory, in the session's state (it survives a hot reload of the mod) and in `<session-id>.pii-mask.json` beside the transcript, so a resumed session restores its placeholders. That file holds the real values in plain text.

## 5. Failing closed

A hook that throws or runs past its 10-second budget is skipped by Claude Code, and for a masking hook that would mean the text goes out as it was. So every masking hook has a `.catch` that answers in its place:

| What goes wrong | What happens |
| --- | --- |
| The model is installing, starting too slowly, or can't run | Requests are answered locally with the reason; rows are stored with their text removed |
| A model call fails or takes over 7 s | That row's text is removed; the system prompt's per-session parts are replaced by a note; injected text is left out |
| Real values can't be restored in a tool call | The call is denied, and Claude can retry it |
| Claude Code is older than 2.1.302, or the saved table can't be read | Every request is answered locally with the reason |

## 6. What is covered

| Data path | Masked? |
| --- | --- |
| Your prompts | ✅ |
| File contents Claude reads, command output, search results (all tool results) | ✅ |
| `CLAUDE.md`, `@file` mentions, reminders, the per-session parts of the system prompt | ✅ |
| Subagents' conversations | ✅ |
| Arguments of `WebSearch`, `WebFetch` and every MCP tool | ✅ kept masked |
| A value the model found once, written again later (any case, any spacing) | ✅ masked even where the model misses it |
| The session's files on disk (`~/.claude/projects/…`) | ✅ when the session ends; the placeholder table saved beside them holds the real values |
| `claude -p` / SDK output | 🟡 masked; restore it with `… --output-format json \| node tools/unmask.mjs` |
| Images and PDFs | ✅ removed before sending, and Claude is told why |
| Personal data the model doesn't recognise | ❌ goes out as written; there are no fallback rules |
| What the rest of the text gives away (a job title, a town, a story) | ❌ masking hides values, not context |

**It fails closed.** If the model isn't running, is still installing, or doesn't answer within 7 seconds, the content is removed instead of sent, and requests are answered locally with the reason. A tool call whose arguments can't be restored is denied.

## 7. What the model looks for (on top of the patterns)

| The model's label | Placeholder |
| --- | --- |
| person | `PERSON` |
| email | `EMAIL` |
| phone number | `PHONE` |
| address | `ADDRESS` |
| iban | `IBAN` |
| bank account number | `ACCOUNT` |
| credit card number | `CARD` |
| social security number, national id number | `ID_NUMBER` |
| passport number | `PASSPORT` |
| date of birth | `BIRTHDATE` |
| ip address | `IP` |

A find counts at a confidence of 0.5 or more. Finds that can't be what their label says are dropped. That covers code the model mistakes for a name (`onMatch`, `RAW_FIELDS`), an "email" without `@`, a "phone number" with fewer than 6 digits, and Claude Code's own words (`Claude`, `Bash`, `JSON`, …). These checks only remove mistakes; they never find anything themselves.
