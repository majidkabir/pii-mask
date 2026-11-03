# Performance

How fast pii-mask is and what it costs, measured November 2026 with [tools/benchmark/perf.py](../tools/benchmark/perf.py): twice on GitHub's machines (the [real-model workflow](../.github/workflows/real-model.yml) runs it after installing the real model), and once in a 2-core cloud container. Each figure is the median of 3 runs; text is unique per run, so the server's cache doesn't answer. Where the two GitHub runs differ, both are given.

## What you notice

| | Linux | Windows | Apple silicon | Intel Mac | 2-core container |
| --- | --- | --- | --- | --- | --- |
| A prompt (200 characters) | 0.3 s | 0.3 s | 0.1–0.2 s | 0.3–0.4 s | 0.3 s |
| A 4 KB file read, first time | 2.7–2.8 s | 3.0 s | 1.4–1.6 s | 3.0–12 s | 2.6 s |
| A 64 KB file read, first time | 39–42 s | 44–45 s | 20–22 s | 33–84 s | 39 s |
| The same text again (cached) | under 0.01 s | under 0.03 s | under 0.01 s | under 0.02 s | under 0.01 s |
| A prompt sent while a 32 KB file is checked | 2.4–2.5 s | 2.5–2.6 s | 0.6–0.8 s | 1.7–3.8 s | 2.1 s |
| A screenshot (1280×800, 141 words) | 5.1–5.4 s | 7.5 s | 3.7–4.9 s | 5.9–8.4 s | 7.0 s |
| A business card (5 words) | 0.5 s | 0.7 s | 0.3–0.6 s | 0.5–0.8 s | 0.5 s |
| A dense scanned A4 page (556 words) | 21–22 s | 28–33 s | 16–28 s | 24–31 s | 24 s ¹ |
| A photo without text (1600×1200) | 12 s | 10–12 s | 5.5–15 s | 7.4–9.5 s | 11 s |
| A 20-page PDF with text | 0.1 s | 0.1–0.2 s | 0.1 s | 0.1–0.2 s | 0.1 s |
| Server memory, peak | 3.9 GB | 3.2 GB ² | 3.3–6.8 GB | 3.5 GB | 4.3 GB |

¹ A real scanned page from the [image benchmark](benchmark.md#5-images-and-scanned-pdfs) (401 words). ² Read wrongly in the first run; fixed for the second.

GitHub's machines for public repositories: Linux and Windows 4 cores, Apple silicon 3 cores, Intel Mac 4 cores. The model reads 1,400–3,500 characters a second on them (fastest on Apple silicon); the Intel Mac runner varied 2.5× between the two runs.

- **Prompts and replies take a fraction of a second.** A prompt is short, and Claude's replies skip the model: they get the patterns and the values already found.
- **Long file reads are slow, once.** The server keeps the model's answer per 1,000-character chunk, so the same file, the system prompt and the reminders Claude Code sends every turn cost nothing after the first time.
- **A short prompt doesn't wait behind a long file.** The server runs the model a few chunks at a time, the request with the least left first, so a prompt sent during a 32 KB file waited 0.6–3.8 s instead of the 10–40 s the file took.
- **Images are the slowest.** OCR of a dense scanned page takes 16–33 s; a photo without text still costs 5–15 s, since OCR has to look for text everywhere.
- **Memory: 3–4 GB, once** for all sessions on the machine (the model, PyTorch and the OCR model). One Apple silicon run peaked at 6.8 GB.

## Limits since 0.7.9

Claude Code ends a plugin's HTTP request after 30 seconds, and until 0.7.9 a model call had 7 seconds, so on these machines a file over roughly 10–20 KB, or a dense scanned page, was removed or dropped the first time instead of masked. Since 0.7.9 the server runs each request as a job that the mod asks for with long polls, and a model call may take 90 s, OCR 60 s. A hook's own 10 s don't count its calls, so the hook isn't cut off while it waits. Checked in Claude Code: a 40 KB file read took 37 s and reached Claude whole and masked; before, it was removed after 30 s.

## Speed-ups tried

| Tried | Result | Kept |
| --- | --- | --- |
| OCR reads 8 words at a time instead of 1 | Same words; a dense page 17 s instead of 21 s | ✅ |
| OCR reads only the part of the page with ink on it (0.7.5) | An A4 letter 1.8 s instead of 7.5 s; same words | ✅ |
| A smaller OCR canvas (1,024 or 1,280 px) | 24–45% faster, but 7–16% of the words read differently | ❌ |
| The model on 1, 8 or 16 chunks at a time instead of 4 | 4 is fastest (1,711 chars/s; 1,326–1,660 for the others) | – |
| The model quantized to 8-bit | 27% faster, but it found nothing (0 of 189 finds) | ❌ |
| Skipping OCR when a quick look finds no text (considered, not built) | Would save most of a photo's 5–15 s, but small text the quick look misses would go out uncovered | ❌ |

## The masking code

The hook's own work (built-in patterns, the values found so far, putting real values back), measured with [mask_speed.ts](../tools/benchmark/mask_speed.ts) in the 2-core container:

| Text | Values already found | Mask | Unmask |
| --- | --- | --- | --- |
| 1,000 characters | 10 / 1,000 | 0.6 / 0.5 ms | under 0.1 ms |
| 10,000 characters | 10 / 1,000 | 2.6 / 1.8 ms | under 0.1 ms |
| 100,000 characters | 10 / 1,000 | 20 / 18 ms | 0.6 ms |
| 1,000,000 characters | 10 / 1,000 | 257 / 223 ms | 8–9 ms |

## Next to Presidio

Microsoft Presidio (2.2.366, spaCy `en_core_web_lg`, Tesseract for images) on the same texts and images, in the same container ([presidio_perf.py](../tools/benchmark/presidio_perf.py)):

| | pii-mask | Presidio |
| --- | --- | --- |
| 200 characters | 0.29 s | 0.01 s |
| 4 KB | 2.6 s | 0.09 s |
| 64 KB | 39 s | 2.6 s |
| Screenshot | 7.0 s | 2.9 s |
| Dense scanned page | 24 s | 6.9 s |
| Photo without text | 11 s | 1.3 s |
| Start-up | 10–17 s (model) | 3.7 s |

Presidio is 15–25 times faster on text and 3–8 times faster on images. It also finds less: on the same benchmarks it hid 86–91% of names where pii-mask hid 90–95%, and on page images 16–23% of the personal data where pii-mask hid 79–80% ([benchmark](benchmark.md)). pii-mask spends the time on a transformer model and EasyOCR, on your CPU.
