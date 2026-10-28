import { describe, expect, test } from 'claude-code/testing'

import {
  cleanTranscript,
  compareVersions,
  findRules,
  looksLikeName,
  mask,
  nerFinds,
  parseVaultFile,
  StreamUnmasker,
  toolPolicy,
  DEFAULT_MASKED_TOOLS,
  unmask,
  unmaskDeep,
  valueScanner,
  Vault,
  vaultFileFor,
} from '../hooks/masker'

const ph = (label: string, n: number | string, marker = 'k3f9') => `<<${label}_${n}.${marker}>>`
const find = (value: string, label: string) => ({ value, label })

describe('finds', () => {
  test('model labels map to placeholder labels; unknown labels and low scores are dropped', () => {
    expect(
      nerFinds([
        { text: 'Lars Holm', label: 'person', score: 0.9 },
        { text: '123456-7890', label: 'social security number', score: 0.8 },
        { text: '987654-3210', label: 'national id number', score: 0.8 },
        { text: 'Aarhus', label: 'location', score: 0.9 },
        { text: 'Anna Berg', label: 'person', score: 0.2 },
      ]),
    ).toEqual([find('Lars Holm', 'PERSON'), find('123456-7890', 'ID_NUMBER'), find('987654-3210', 'ID_NUMBER')])
  })

  test('shapes: each label keeps only finds that can be what it says', () => {
    const cases: [string, string, boolean][] = [
      ['onMatch', 'person', false], ['RAW_FIELDS', 'person', false], ['lars', 'person', false], ['Mette Sørensen', 'person', true],
      ['lars@holm.dk', 'email', true], ['holm.dk', 'email', false],
      ['+45 12 34 56 78', 'phone number', true], ['call me', 'phone number', false], ['v1.2', 'phone number', false],
      ['DK50 0040 0440 1162 43', 'iban', true], ['DK50', 'iban', false],
      ['4111 1111 1111 1111', 'credit card number', true], ['4111', 'credit card number', false],
      ['192.168.1.20', 'ip address', true], ['localhost', 'ip address', false],
      ['Havnegade 12, 1058 København K', 'address', true], ['{ a: 1 }', 'address', false],
    ]
    for (const [text, label, kept] of cases) expect([text, nerFinds([{ text, label }]).length === 1]).toEqual([text, kept])
  })

  test('Claude Code\'s own words and placeholders are never finds', () => {
    expect(nerFinds([{ text: 'Claude', label: 'person' }, { text: 'Bash', label: 'person' }, { text: ph('PERSON', 1), label: 'person' }])).toEqual([])
    expect(nerFinds([{ text: 'noreply' + '@anthropic.com', label: 'email' }, { text: 'Claude Sonnet', label: 'person' }])).toEqual([])
  })

  test('looksLikeName', () => {
    expect(looksLikeName('Hansen-Berg')).toBe(true)
    expect(looksLikeName("O'Brien")).toBe(true)
    expect(looksLikeName('NerSettings')).toBe(false)
    expect(looksLikeName('x = 1')).toBe(false)
    // Pronouns, greetings and roles the model sometimes tags as people.
    for (const w of ['We', 'You', 'Your name', 'Dear', 'Hi', 'Vi', 'Kære', 'Wir']) expect([w, looksLikeName(w)]).toEqual([w, false])
    expect(looksLikeName('Sofie')).toBe(true)
    for (const w of ['Claude', 'Claude Sonnet', 'Claude Opus 5.5', 'Claude Code']) expect([w, looksLikeName(w)]).toEqual([w, false])
    expect(looksLikeName('Claudia Berg')).toBe(true)
    expect(looksLikeName('Dear Sofie')).toBe(true)
  })

  test('an address needs a number', () => {
    expect(nerFinds([{ text: 'home address', label: 'address' }, { text: 'Your home address', label: 'address' }, { text: 'Hellerup', label: 'address' }])).toEqual([])
    expect(nerFinds([{ text: 'Strandvejen 112, 2900 Hellerup', label: 'address' }])).toHaveLength(1)
  })
})

describe('masking', () => {
  test('finds become placeholders; each spelling gets its own token, restored exactly', () => {
    const v = new Vault('k3f9')
    const out = mask('Lars Holm, LARS HOLM and lars  holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    expect(out).toBe(`${ph('PERSON', 1)}, ${ph('PERSON', '1-2')} and ${ph('PERSON', '1-3')}`)
    expect(unmask(out, v)).toBe('Lars Holm, LARS HOLM and lars  holm')
  })

  test('whole words only', () => {
    const v = new Vault('k3f9')
    expect(mask('Anna, Annabel', findRules([find('Anna', 'PERSON')], v), v)).toBe(`${ph('PERSON', 1)}, Annabel`)
  })

  test('values that start or end with punctuation still match', () => {
    const v = new Vault('k3f9')
    expect(mask('call +45 12 34 56 78.', findRules([find('+45 12 34 56 78', 'PHONE')], v), v)).toBe(`call ${ph('PHONE', 1)}.`)
  })

  test('an email is masked whole before the name inside it', () => {
    const v = new Vault('k3f9')
    const out = mask('lars.holm@x.dk', findRules([find('lars.holm@x.dk', 'EMAIL'), find('Lars', 'PERSON')], v), v)
    expect(out).toBe(ph('EMAIL', 1))
  })

  test('values found earlier are masked without being found again', () => {
    const v = new Vault('k3f9')
    mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    expect(mask('Bye Lars Holm', findRules([], v), v)).toBe(`Bye ${ph('PERSON', 1)}`)
  })

  test('text that is already a placeholder is left alone', () => {
    const v = new Vault('k3f9')
    const once = mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    expect(mask(once, findRules([], v), v)).toBe(once)
  })

  test('only this session\'s marker is restored', () => {
    const v = new Vault('k3f9')
    mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    expect(unmask(`${ph('PERSON', 1)} ${ph('PERSON', 1, 'zzzz')}`, v)).toBe(`Lars Holm ${ph('PERSON', 1, 'zzzz')}`)
  })

  test('unmaskDeep restores nested arguments and skips the envelope', () => {
    const v = new Vault('k3f9')
    mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    const p = ph('PERSON', 1)
    expect(unmaskDeep({ tool: p, a: [{ b: p }] }, v, new Set(['tool']))).toEqual({ tool: p, a: [{ b: 'Lars Holm' }] })
  })

  test('a stream holds back a split placeholder', () => {
    const v = new Vault('k3f9')
    mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    const s = new StreamUnmasker(v)
    const p = ph('PERSON', 1)
    expect(s.push(`Hi ${p.slice(0, 6)}`)).toBe('Hi ')
    expect(s.push(`${p.slice(6)}!`)).toBe('Lars Holm!')
    expect(s.flush()).toBe('')
  })

  test('the scanner handles many values in one pass', () => {
    const names = Array.from({ length: 5000 }, (_, i) => `Person Number${i}`)
    const scan = valueScanner(names)
    expect(scan('x Person Number4999 y', m => `[${m}]`)).toBe('x [Person Number4999] y')
  })
})

describe('the saved table and local copies', () => {
  test('vault file name and format', () => {
    expect(vaultFileFor('/p/s1.jsonl')).toBe('/p/s1.pii-mask.json')
    expect(() => parseVaultFile('{"version":2}')).toThrow('not a pii-mask vault file')
    const v = new Vault('k3f9')
    mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    const again = new Vault('k3f9')
    again.load(parseVaultFile(JSON.stringify({ version: 1, marker: 'k3f9', entries: v.entries() })).entries)
    expect(mask('lars holm', findRules([], again), again)).toBe(ph('PERSON', '1-2'))
  })

  test('cleanTranscript masks the raw copies and keeps paths', () => {
    const v = new Vault('k3f9')
    mask('Lars Holm', findRules([find('Lars Holm', 'PERSON')], v), v)
    const lines = [
      JSON.stringify({ type: 'queue-operation', content: 'ask Lars Holm' }),
      JSON.stringify({ type: 'user', toolUseResult: { filePath: '/home/Lars Holm/x', stdout: 'Lars Holm' } }),
      JSON.stringify({ type: 'assistant', message: 'untouched' }),
    ].join('\n')
    const { text, changedLines } = cleanTranscript(lines, v)
    expect(changedLines).toBe(2)
    expect(text).toContain('/home/Lars Holm/x')
    expect(text.split('\n')[2]).toBe(lines.split('\n')[2])
    expect(text).toContain(`ask ${ph('PERSON', 1)}`)
  })
})

describe('policy', () => {
  test('web and MCP tools keep placeholders', () => {
    const keep = toolPolicy(DEFAULT_MASKED_TOOLS)
    expect(['WebSearch', 'WebFetch', 'mcp__slack__post'].every(keep)).toBe(true)
    expect(['Bash', 'Edit', 'Write'].some(keep)).toBe(false)
  })

  test('compareVersions', () => {
    expect(compareVersions('2.1.302', '2.1.302')).toBe(0)
    expect(compareVersions('2.1.296', '2.1.302')).toBe(-1)
    expect(compareVersions('2.2.0-beta', '2.1.302')).toBe(1)
  })
})
