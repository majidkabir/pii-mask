# Performance

How fast pii-mask is and what it costs, measured November 2026 with [tools/benchmark/perf.py](../tools/benchmark/perf.py) on GitHub's machines (the [real-model workflow](../.github/workflows/real-model.yml) runs it after installing the real model) and in a 2-core cloud container. Each figure is the median of 3 runs; text is unique per run, so the server's cache doesn't answer.

## What you notice

| | Linux | Windows | Apple silicon | Intel Mac |
| --- | --- | --- | --- | --- |
| A prompt (200 characters) | 0.29 s | 0.33 s | 0.22 s | 0.29 s ¹ |
| A 4 KB file read, first time | 2.7 s | 3.1 s | 1.6 s | 3.0 s |
| A 64 KB file read, first time | 39 s | 44 s | 22 s | 33 s |
| The same text again (cached) | under 0.03 s | under 0.03 s | under 0.02 s | under 0.01 s |
| A prompt sent while a 32 KB file is checked | 2.5 s | 2.5 s | 0.8 s | 1.7 s |
| A screenshot (1280×800, 141 words) | 5.1 s | 7.5 s | 4.9 s | 5.9 s |
| A business card (5 words) | 0.5 s | 0.7 s | 0.6 s | 0.5 s |
| A dense scanned A4 page (556 words) | 21 s | 33 s | 28 s | 24 s |
| A photo without text (1600×1200) | 12 s | 10 s | 15 s | 7 s |
| A 20-page PDF with text | 0.11 s | 0.12 s | 0.08 s | 0.15 s |
| Server memory, peak | 3.9 GB | ² | 3.3 GB | 3.5 GB |

¹ The prompt on the Intel Mac was the run's first request: 1.1 s; the second was 0.29 s. ² Read wrongly on Windows in that run; fixed since.

GitHub's runners: Linux and Windows 4 cores, macOS 3–4 cores. The model reads 1,400–2,900 characters a second there (2,900 on Apple silicon).

- **Prompts and replies** take a fraction of a second: a prompt is short, and Claude's replies skip the model (they get the patterns and the values already found).
- **Long file reads** are the slow part, once: the server keeps the model's answer per 1,000-character chunk, so the same file, the system prompt and the reminders Claude Code sends every turn cost nothing after the first time.
- **A short prompt doesn't wait behind a long file**: the server runs the model a few chunks at a time, the request with the least left first, so the prompt above waited 0.8–2.5 s instead of the 20–40 s the file took.
- **Images are the slowest**: OCR of a dense scanned page takes 20–33 s.

## The masking code

The hook's own work (built-in patterns, the values found so far, putting real values back), measured with [mask_speed.ts](../tools/benchmark/mask_speed.ts):

MASK_TABLE

## Next to Presidio

PRESIDIO_TABLE
