import { describe, expect, test } from 'claude-code/testing'
import { mask, valueSpans, Vault } from '../hooks/masker'
import { boxLabel, pdfText, spanBoxes } from '../hooks/media'
import { OCR_PATTERNS, PATTERNS } from '../hooks/patterns'

const ocrRules = () => {
  const [email, ...rest] = OCR_PATTERNS
  return [email!, ...PATTERNS, ...rest]
}

describe('text read from an image', () => {
  const cases: [string, string, string][] = [
    ['Mail: sofie lindqvist@example. dk', 'EMAIL', 'sofie lindqvist@example. dk'],
    ['E-Mail: lukas- schneider@beispiel:. de', 'EMAIL', 'lukas- schneider@beispiel:. de'], // dots read as "-" and ":."
    ['IBAN: DK5O00400440116243', 'IBAN', 'DK5O00400440116243'],
    ['Phone 28 47 l9 63 now', 'NUMBER', '28 47 l9 63'],
    ['CPR 14O387-1234', 'ID_NUMBER', '14O387-1234'], // the text rule for a named ID number catches it first
  ]
  for (const [text, label, value] of cases) {
    test(`${label}: ${text}`, () => {
      const v = new Vault('k3f9')
      const masked = mask(text, ocrRules(), v)
      expect(masked).not.toContain(value)
      expect(v.entries().map(e => [e.label, e.value])).toContainEqual([label, value])
    })
  }

  test('ordinary words and short numbers stay', () => {
    const v = new Vault('k3f9')
    expect(mask('Order shipped in 2024 to the warehouse', ocrRules(), v)).toBe('Order shipped in 2024 to the warehouse')
  })

  test('valueSpans finds each masked value in the original line', () => {
    const v = new Vault('k3f9')
    const line = 'To: anna@' + 'berg.se and ANNA@' + 'BERG.SE'
    const masked = mask(line, PATTERNS, v)
    const spans = valueSpans(line, masked, v)
    expect(spans.map(s => line.slice(s.start, s.end))).toEqual(['anna@' + 'berg.se', 'ANNA@' + 'BERG.SE'])
  })
})

describe('boxes', () => {
  const line = {
    text: 'Customer: Lars Holm',
    words: [
      { text: 'Customer:', box: [0, 0, 90, 20] as [number, number, number, number], start: 0 },
      { text: 'Lars', box: [100, 2, 140, 22] as [number, number, number, number], start: 10 },
      { text: 'Holm', box: [145, 0, 185, 20] as [number, number, number, number], start: 15 },
    ],
  }
  test('a span over whole words gets one box around them', () => {
    expect(spanBoxes(line, [{ start: 10, end: 19, label: 'PERSON_1' }])).toEqual([{ box: [100, 0, 185, 22], label: 'PERSON_1' }])
  })
  test('a span over part of a word gets its share of the width', () => {
    const one = { text: 'Mail:anna@x.dk', words: [{ text: 'Mail:anna@x.dk', box: [0, 0, 140, 20] as [number, number, number, number], start: 0 }] }
    expect(spanBoxes(one, [{ start: 5, end: 14, label: 'EMAIL_1' }])).toEqual([{ box: [50, 0, 140, 20], label: 'EMAIL_1' }])
  })
  test('labels drop the spelling number', () => {
    expect(boxLabel('PERSON_1-2')).toBe('PERSON_1')
  })
})

describe('PDF text', () => {
  test('pages are numbered, and a cut is said', () => {
    const t = pdfText({ pages: ['a', 'b'], page_count: 2, truncated: true }, 'x.pdf')
    expect(t).toContain('[PDF "x.pdf", 2 pages')
    expect(t).toContain('--- page 2 ---\nb')
    expect(t).toContain('The rest of the PDF was left out')
  })
})
