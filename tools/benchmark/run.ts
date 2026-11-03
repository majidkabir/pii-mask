// Runs pii-mask's masking on benchmark documents exactly as the mod does for a
// prompt or a file: the local model (/detect, the mod's labels and threshold),
// nerFinds' shape checks, then [vault values, built-in patterns, model finds].
// One fresh vault per document. Prints the masked character spans.
//   bun run.ts in.jsonl out.jsonl      (in: {"id","text"} per line)
import { findRules, mask, nerFinds, PII_LABELS, THRESHOLD, Vault } from '../../hooks/masker'
import { PATTERNS } from '../../hooks/patterns'

const [inFile, outFile] = process.argv.slice(2)
const LABELS = Object.keys(PII_LABELS)
const PH = /<<([A-Z][A-Z0-9_]*_\d+(?:-\d+)?)\.([a-z0-9]{4})>>/g

async function detect(texts: string[]) {
  const res = await fetch('http://127.0.0.1:8766/detect', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ texts, labels: LABELS, threshold: THRESHOLD }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return ((await res.json()) as { results: { text: string; label: string; score: number }[][] }).results
}

/** Splits a long document into pieces of at most ~8,000 characters at line breaks, as a file read arrives in pieces. */
function pieces(text: string, max = 8000): string[] {
  const out: string[] = []
  let cur = ''
  for (const line of text.split(/(?<=\n)/)) {
    if (cur && cur.length + line.length > max) { out.push(cur); cur = '' }
    cur += line
  }
  if (cur) out.push(cur)
  return out
}

/** Where each placeholder's value sits in the original: walks the masked text against it. */
function spansOf(original: string, masked: string, vault: Vault) {
  const parts: ({ lit: string } | { token: string; marker: string })[] = []
  let last = 0
  for (const m of masked.matchAll(PH)) {
    if (m.index! > last) parts.push({ lit: masked.slice(last, m.index) })
    parts.push({ token: m[1]!, marker: m[2]! })
    last = m.index! + m[0].length
  }
  if (last < masked.length) parts.push({ lit: masked.slice(last) })
  const spans: [number, number, string][] = []
  let pos = 0
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i]!
    if ('lit' in p) {
      if (!original.startsWith(p.lit, pos)) throw new Error(`alignment lost at ${pos}`)
      pos += p.lit.length
      continue
    }
    const next = parts[i + 1]
    let end: number
    if (next && 'lit' in next) end = original.indexOf(next.lit, pos)
    else {
      const value = vault.resolve(p.token, p.marker) ?? ''
      end = pos + value.length // adjacent placeholders: the value as it was matched
    }
    if (end < pos) throw new Error(`no end for ${p.token}`)
    spans.push([pos, end, p.token.replace(/_\d+(-\d+)?$/, '')])
    pos = end
  }
  return spans
}

const lines = (await Bun.file(inFile!).text()).split('\n').filter(Boolean)
const out: string[] = []
let n = 0
const t0 = Date.now()
for (const line of lines) {
  const doc = JSON.parse(line) as { id: string; text: string }
  const vault = new Vault('bnch')
  const spans: [number, number, string][] = []
  let offset = 0
  for (const piece of pieces(doc.text)) {
    const results = await detect([piece])
    const rules = [...findRules([], vault), ...PATTERNS, ...findRules(nerFinds(results[0] ?? []), vault)]
    const masked = mask(piece, rules, vault)
    for (const [s, e, l] of spansOf(piece, masked, vault)) spans.push([s + offset, e + offset, l])
    offset += piece.length
  }
  out.push(JSON.stringify({ id: doc.id, spans }))
  if (++n % 10 === 0) console.error(`${n}/${lines.length} docs, ${((Date.now() - t0) / 1000).toFixed(0)} s`)
}
await Bun.write(outFile!, out.join('\n') + '\n')
console.error(`done: ${n} docs in ${((Date.now() - t0) / 1000).toFixed(0)} s`)
