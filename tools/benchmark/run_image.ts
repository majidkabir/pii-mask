// Runs pii-mask's image path on page images, reading each page once (/media/ocr)
// and computing the boxes two ways from the same OCR output:
//   v078: each line to the model on its own, masked line by line (readImage in 0.7.8)
//   v079: the lines as one text to the model, masked by maskLines
//   both: the model on the lines as one text and on each line, masked by maskLines (readImage since 0.7.9)
// Patterns: IMAGE_PATTERNS (OCR's email and IBAN shapes, the text patterns, OCR's
// long numbers), then the model's finds, as readImage. One fresh vault per page and way.
//   bun run_image.ts PAGES_DIR gold.json OUT_PREFIX clean|scan   (PAGES_DIR absolute: the server opens the files)
import { existsSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { findRules, mask, nerFinds, PII_LABELS, THRESHOLD, valueSpans, Vault } from '../../hooks/masker'
import { boxLabel, maskLines, spanBoxes, type OcrLine } from '../../hooks/media'
import { IMAGE_PATTERNS } from '../../hooks/patterns'

const [dir, goldFile, prefix, kind = 'clean'] = process.argv.slice(2)
const post = async (path: string, body: unknown) => {
  const res = await fetch('http://127.0.0.1:8766' + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`)
  return res.json() as Promise<any>
}
const detect = async (texts: string[]) => (texts.length ? (await post('/detect', { texts, labels: Object.keys(PII_LABELS), threshold: THRESHOLD })).results : []) as any[]
const rulesFor = (finds: ReturnType<typeof nerFinds>, vault: Vault) => [...IMAGE_PATTERNS, ...findRules(finds, vault)]

const gold = (await Bun.file(goldFile!).json())[kind!] as { page: string }[]
const out: Record<string, Record<string, unknown>> = { v078: {}, v079: {}, both: {} }
const t0 = Date.now()
let n = 0
for (const { page } of gold) {
  const cacheFile = `${dir}/../ocr_cache/${page}.json`
  mkdirSync(dirname(cacheFile), { recursive: true })
  let ocr: { width: number; height: number; lines: OcrLine[]; ms: number }
  if (existsSync(cacheFile)) ocr = await Bun.file(cacheFile).json()
  else {
    const t = Date.now()
    ocr = await post('/media/ocr', { path: `${dir}/${page}` })
    ocr.ms = Date.now() - t
    await Bun.write(cacheFile, JSON.stringify(ocr))
  }
  const lines = ocr.lines
  const meta = { ocrWidth: ocr.width, ocrHeight: ocr.height }
  {
    const t = Date.now()
    const vault = new Vault('bnch')
    const results = await detect(lines.map(l => l.text))
    const rules = rulesFor(lines.flatMap((_, i) => nerFinds(results[i] ?? [])), vault)
    const boxes: number[][] = []
    for (const line of lines) {
      const m = mask(line.text, rules, vault)
      if (m === line.text) continue
      for (const b of spanBoxes(line, valueSpans(line.text, m, vault).map(v => ({ start: v.start, end: v.end, label: boxLabel(v.token) })))) boxes.push(b.box)
    }
    out.v078![page] = { boxes, ms: ocr.ms + Date.now() - t, ...meta }
  }
  {
    const t = Date.now()
    const vault = new Vault('bnch')
    const results = await detect(lines.length ? [lines.map(l => l.text).join('\n')] : [])
    const boxes = maskLines(lines, rulesFor(nerFinds(results[0] ?? []), vault), vault).boxes.map(b => b.box)
    out.v079![page] = { boxes, ms: ocr.ms + Date.now() - t, ...meta }
  }
  {
    const t = Date.now()
    const vault = new Vault('bnch')
    const joined = await detect(lines.length ? [lines.map(l => l.text).join('\n')] : [])
    const perLine = await detect(lines.map(l => l.text))
    const finds = [...nerFinds(joined[0] ?? []), ...lines.flatMap((_, i) => nerFinds(perLine[i] ?? []))]
    const boxes = maskLines(lines, rulesFor(finds, vault), vault).boxes.map(b => b.box)
    out.both![page] = { boxes, ms: ocr.ms + Date.now() - t, ...meta }
  }
  if (++n % 10 === 0) console.error(`${kind} ${n}/${gold.length} pages, ${((Date.now() - t0) / 1000).toFixed(0)} s`)
}
for (const [v, data] of Object.entries(out)) await Bun.write(`${prefix}_${v}_${kind}.json`, JSON.stringify(data))
console.error(`done: ${n} ${kind} pages in ${((Date.now() - t0) / 1000).toFixed(0)} s`)
