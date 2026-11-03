import { describe, expect, test } from 'claude-code/testing'
import { findRules, mask, valueSpans, Vault } from '../hooks/masker'
import { boxLabel, maskLines, pdfText, spanBoxes } from '../hooks/media'
import { IMAGE_PATTERNS, PATTERNS } from '../hooks/patterns'

const ocrRules = () => IMAGE_PATTERNS

describe('text read from an image', () => {
  const cases: [string, string, string][] = [
    ['Mail: sofie lindqvist@example. dk', 'EMAIL', 'sofie lindqvist@example. dk'],
    ['E-Mail: lukas- schneider@beispiel:. de', 'EMAIL', 'lukas- schneider@beispiel:. de'], // dots read as "-" and ":."
    ['SzaszJanka@cuvox de', 'EMAIL', 'SzaszJanka@cuvox de'], // the dot lost entirely
    ['email? Janina Tokareva@superrito. com', 'EMAIL', 'Janina Tokareva@superrito. com'], // split at a capital
    ['Account DK89 3704 0044 0532 0130 00 refund', 'IBAN', 'DK89 3704 0044 0532 0130 00'], // checksum fails (a misread); not a phone after "00"
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

  test('a name before an email in the same line stays a name, not part of the address', () => {
    const v = new Vault('k3f9')
    const masked = mask('Henrik Bach henrik.bach@bachbyg.dk +45 31 22 48 90', ocrRules(), v)
    expect(masked).toMatch(/^Henrik Bach <<EMAIL_1\.k3f9>> /)
  })

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

describe('masking an image\'s lines', () => {
  const line = (text: string, y: number) => {
    let at = 0
    const words = text.split(' ').map(w => {
      const word = { text: w, box: [at * 10, y, (at + w.length) * 10, y + 20] as [number, number, number, number], start: at }
      at += w.length + 1
      return word
    })
    return { text, words }
  }

  test('a value over several lines is one value, with a box on each of its lines', () => {
    const v = new Vault('k3f9')
    const lines = [line('Address:', 0), line('Brucker Bundesstrasse 31', 30), line('Suite 245', 60), line('Thanks', 90)]
    const rules = findRules([{ value: 'Brucker Bundesstrasse 31\nSuite 245', label: 'ADDRESS' }], v)
    const { masked, boxes } = maskLines(lines, rules, v)
    expect(masked.join('\n')).toMatch(/^Address:\n<<ADDRESS_1\.k3f9>>\nThanks$/)
    expect(boxes.map(b => b.box)).toEqual([[0, 30, 240, 50], [0, 60, 90, 80]])
  })
})

