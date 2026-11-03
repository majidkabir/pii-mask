/**
 * Images and PDFs, the parts that need no engine: where to draw boxes over the
 * words OCR read, and the text a PDF is sent as. register.tsx calls the local
 * server for the OCR, the PDF text and the drawing.
 */

import { mask, valueSpans, type Rule, type Vault } from './masker'

/** A word OCR read: its text, its box in the image, and where it starts in its line's text. */
export type OcrWord = { text: string; box: [number, number, number, number]; start: number }
export type OcrLine = { text: string; words: OcrWord[] }
export type Box = { box: [number, number, number, number]; label: string }

/**
 * Masks an image's lines as one text, as a file's text is masked: the model
 * sees each line with the lines around it, and a value over several lines (an
 * address on a letter) is one value. Then a box over each masked value's words,
 * line by line. `masked` is the text Claude gets, line by line.
 */
export function maskLines(lines: readonly OcrLine[], rules: readonly Rule[], vault: Vault): { masked: string[]; boxes: Box[] } {
  const text = lines.map(l => l.text).join('\n')
  const masked = mask(text, rules, vault)
  if (masked === text) return { masked: masked.split('\n'), boxes: [] }
  const spans = valueSpans(text, masked, vault)
  const boxes: Box[] = []
  let offset = 0
  for (const line of lines) {
    const end = offset + line.text.length
    const here = spans
      .filter(v => v.start < end && v.end > offset)
      .map(v => ({ start: Math.max(v.start, offset) - offset, end: Math.min(v.end, end) - offset, label: boxLabel(v.token) }))
    if (here.length) boxes.push(...spanBoxes(line, here))
    offset = end + 1
  }
  return { masked: masked.split('\n'), boxes }
}

/**
 * The boxes covering spans of a line's text (one per span, around the words
 * it touches). A span that covers part of a word gets the matching share of
 * the word's width, since OCR gives boxes per word, not per letter.
 */
export function spanBoxes(line: OcrLine, spans: readonly { start: number; end: number; label: string }[]): Box[] {
  const out: Box[] = []
  for (const s of spans) {
    let x0 = Infinity
    let y0 = Infinity
    let x1 = -Infinity
    let y1 = -Infinity
    for (const w of line.words) {
      const ws = w.start
      const we = w.start + w.text.length
      const a = Math.max(s.start, ws)
      const b = Math.min(s.end, we)
      if (a >= b) continue
      const [bx0, by0, bx1, by1] = w.box
      const width = bx1 - bx0
      const len = Math.max(1, w.text.length)
      x0 = Math.min(x0, bx0 + Math.floor((width * (a - ws)) / len))
      x1 = Math.max(x1, bx0 + Math.ceil((width * (b - ws)) / len))
      y0 = Math.min(y0, by0)
      y1 = Math.max(y1, by1)
    }
    if (x0 < x1) out.push({ box: [x0, y0, x1, y1], label: s.label })
  }
  return out
}

/** A placeholder's short form for a box drawn in an image ("PERSON_1"): the full one is in the text block. */
export const boxLabel = (token: string) => token.replace(/-\d+$/, '')

/** The text a PDF is sent as, page by page. */
export function pdfText(p: { pages: string[]; page_count: number; truncated?: boolean }, title?: string): string {
  const name = title ? ` "${title}"` : ''
  const head = `[PDF${name}, ${p.page_count} page${p.page_count === 1 ? '' : 's'}: sent as its text by pii-mask, so the personal data in it could be masked. Layout and pictures are not included.]`
  const body = p.pages.map((t, i) => (p.pages.length > 1 ? `--- page ${i + 1} ---\n${t.trim()}` : t.trim())).join('\n\n')
  const tail = p.truncated ? '\n\n[The rest of the PDF was left out: too long to check in time. Ask the user for the pages you need.]' : ''
  return `${head}\n\n${body}${tail}`
}

/** The text block sent with a redacted image. */
export function imageText(lines: readonly string[]): string {
  return '[Text pii-mask read in the image above, with personal data replaced by placeholders:]\n' + lines.join('\n')
}
