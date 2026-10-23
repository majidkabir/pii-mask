// Pure masking logic: no `$`, so it is unit-testable on its own.
// Values are found by a local NER model (see register.tsx and server/); this
// module turns what the model found into placeholders and back.

import type { VaultEntry } from '../types'

/**
 * The oldest Claude Code this mod is tested on. On an older build a hook or
 * API it relies on might not exist, and nothing would be masked, so older
 * builds are blocked.
 */
export const MIN_CLAUDE_CODE = '2.1.302'

/** Compares dotted release numbers ("2.1.302"); anything after a '-' is ignored. */
export function compareVersions(a: string, b: string): number {
  const pa = a.split('-')[0]?.split('.').map(Number) ?? []
  const pb = b.split('-')[0]?.split('.').map(Number) ?? []
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d !== 0) return Math.sign(d)
  }
  return 0
}

/**
 * What the model is asked to find, and the placeholder label each find gets.
 * The labels are the model's own (urchade/gliner_multi_pii-v1). Two kinds of
 * national ID share one placeholder label.
 */
export const PII_LABELS: Readonly<Record<string, string>> = {
  person: 'PERSON',
  email: 'EMAIL',
  'phone number': 'PHONE',
  address: 'ADDRESS',
  iban: 'IBAN',
  'bank account number': 'ACCOUNT',
  'credit card number': 'CARD',
  'social security number': 'ID_NUMBER',
  'national id number': 'ID_NUMBER',
  'passport number': 'PASSPORT',
  'date of birth': 'BIRTHDATE',
  'ip address': 'IP',
}

/** Lowest model confidence that counts as a find. */
export const THRESHOLD = 0.5

/** Content block kinds that carry pixels or a document, which text masking cannot see into. */
export const MEDIA_BLOCKS = new Set(['image', 'document'])

export const UNCHECKED_NOTE =
  '[pii-mask could not check this content for personal data, so it was removed before it was stored or sent. ' +
  'If the local model was still starting, ask the user to send it again; otherwise ask for a smaller excerpt.]'

export const MEDIA_NOTE =
  '[pii-mask removed an image or PDF here: media cannot be checked for personal data, so it is not sent. ' +
  'Ask the user to describe it, or to extract its text into a file.]'

/**
 * Tools that can send their arguments to a server keep placeholders: the web
 * tools and every MCP tool.
 */
export const DEFAULT_MASKED_TOOLS = ['WebSearch', 'WebFetch', 'mcp__*']

const reEsc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const globToRegex = (glob: string) => new RegExp(`^${glob.split('*').map(reEsc).join('.*')}$`)

/** Decides, per tool name, whether its arguments keep their placeholders. */
export function toolPolicy(masked: readonly string[]): (tool: string) => boolean {
  const keep = masked.map(globToRegex)
  return tool => keep.some(r => r.test(tool))
}

/* ------------------------------------------------------------------ finds */

/** An entity as the NER server answers it. */
export type NerEntity = { text: string; label: string; score?: number }

/** One thing the model found: the text as written and its placeholder label. */
export type NerFind = { value: string; label: string }

/**
 * Words NER models tag as a person or an organization that are Claude Code's
 * own (its tools, the model, file formats): never masked, in any case.
 */
export const ALWAYS_IGNORED = [
  'Claude', 'Claude Code', 'Anthropic', 'Bash', 'Read', 'Write', 'Edit', 'MultiEdit', 'Grep', 'Glob', 'LS',
  'WebFetch', 'WebSearch', 'Agent', 'Task', 'TodoWrite', 'NotebookEdit', 'Skill', 'MCP', 'API', 'CLI', 'JSON',
  'YAML', 'CSV', 'PDF', 'HTML', 'HTTP', 'HTTPS', 'URL', 'README', 'Git', 'GitHub', 'npm', 'Node',
  'localhost', '127.0.0.1', '0.0.0.0', '::1',
]

/** Placeholder labels: text that explains placeholders mentions them. */
const LABEL_WORDS = [...new Set(Object.values(PII_LABELS))]

/** How a value is keyed: any case, any run of whitespace. */
const exactKey = (v: string) => v.toLowerCase().replace(/\s+/g, ' ')

/**
 * Whether a find can be a person's name rather than code the model mistook
 * for one: letters, digits, spaces and . , & ' ’ / - only, starting with a
 * capital, and no word in camelCase or snake_case ("onMatch", "RAW_FIELDS").
 */
export function looksLikeName(value: string): boolean {
  if (!/^\p{Lu}/u.test(value)) return false
  if (/[^\p{L}\p{M}\p{N} .,&'’/-]/u.test(value) || value.includes('&&')) return false
  return value.split(/[\s/-]+/).every(w => !/\p{Ll}\p{Lu}/u.test(w))
}

const digits = (v: string) => v.replace(/\D/g, '').length

/**
 * Per placeholder label, what a find must look like to be kept. These only
 * drop the model's mistakes (code, a lone word); they never find anything.
 */
const SHAPE: Record<string, (v: string) => boolean> = {
  PERSON: looksLikeName,
  ADDRESS: v => /\p{L}/u.test(v) && v.length >= 5 && !/[^\p{L}\p{M}\p{N} .,'’/#-]/u.test(v),
  EMAIL: v => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v),
  PHONE: v => digits(v) >= 6 && !/[^\d\s+().-]/.test(v),
  IBAN: v => /^[A-Za-z]{2}\d{2}/.test(v.replace(/\s/g, '')) && digits(v) >= 8,
  ACCOUNT: v => digits(v) >= 5,
  CARD: v => digits(v) >= 12,
  ID_NUMBER: v => digits(v) >= 5,
  PASSPORT: v => digits(v) >= 4 && !/\s{2,}/.test(v),
  BIRTHDATE: v => digits(v) >= 1,
  IP: v => /^\d{1,3}(\.\d{1,3}){3}$/.test(v) || (/^[0-9a-f:]+$/i.test(v) && v.includes(':') && v.length >= 6),
}

/**
 * Turns the server's entities into finds: mapped to placeholder labels, below
 * the threshold left out, and dropped when they can't be what the label says,
 * are one of Claude Code's own words, or are (part of) a placeholder.
 */
export function nerFinds(entities: readonly NerEntity[]): NerFind[] {
  const ignore = new Set([...ALWAYS_IGNORED, ...LABEL_WORDS].map(exactKey))
  const out: NerFind[] = []
  for (const e of entities) {
    const label = PII_LABELS[e.label]
    const value = typeof e.text === 'string' ? e.text.trim().replace(/^[\s"'`([{<]+|[\s"'`)\]}>,;:]+$/g, '') : ''
    if (!label || value.replace(/[^\p{L}\p{N}]/gu, '').length < 2) continue
    if (typeof e.score === 'number' && e.score < THRESHOLD) continue
    if (value.includes('<<') || value.includes('>>') || ignore.has(exactKey(value))) continue
    if (!(SHAPE[label]?.(value) ?? true)) continue
    out.push({ value, label })
  }
  return out
}

/* ------------------------------------------------------------------ rules */

export type Rule = {
  label: string
  /** Finds the values and replaces each with `onMatch`'s answer. */
  scan: (text: string, onMatch: (match: string) => string) => string
  normalize: (value: string) => string
}

/** A node of the value trie: lower-cased characters to the next node. */
type TrieNode = { next: Map<string, TrieNode>; end: boolean }
const newNode = (): TrieNode => ({ next: new Map(), end: false })
const WORD_CHAR = /[\p{L}\p{N}]/u
const isWordChar = (c: string | undefined): boolean =>
  c !== undefined && (c < '\u0080' ? /[A-Za-z0-9]/.test(c) : WORD_CHAR.test(c))
const isSpace = (c: string) => /\s/.test(c)

/**
 * Finds the given values in one pass over the text, in any case and with any
 * run of whitespace between words, whole words only; the longest wins.
 */
export function valueScanner(values: Iterable<string>): (text: string, onMatch: (m: string) => string) => string {
  const root = newNode()
  for (const value of values) {
    let node = root
    for (const ch of value.toLowerCase().replace(/\s+/g, ' ')) {
      let n = node.next.get(ch)
      if (!n) node.next.set(ch, (n = newNode()))
      node = n
    }
    node.end = true
  }
  const longestAt = (text: string, i: number): number => {
    let node = root
    let j = i
    let last = -1
    let inSpace = false
    while (j < text.length) {
      const c = text[j] as string
      if (isSpace(c)) {
        if (inSpace) { j++; continue }
        const n = node.next.get(' ')
        if (!n) break
        node = n
        inSpace = true
        j++
        continue
      }
      inSpace = false
      const n = node.next.get(c.toLowerCase())
      if (!n) break
      node = n
      j++
      if (node.end && !(isWordChar(text[j]) && isWordChar(text[j - 1]))) last = j
    }
    return last
  }
  return (text, onMatch) => {
    let out = ''
    let copied = 0
    let i = 0
    while (i < text.length) {
      const c = text[i] as string
      if (!(isWordChar(text[i - 1]) && isWordChar(c)) && root.next.has(c.toLowerCase())) {
        const end = longestAt(text, i)
        if (end > i) {
          out += text.slice(copied, i) + onMatch(text.slice(i, end))
          copied = i = end
          continue
        }
      }
      i++
    }
    return copied === 0 ? text : out + text.slice(copied)
  }
}

/**
 * Rules that mask what the model found in the text at hand plus every value
 * it found earlier this session (the vault), so a value found once stays
 * masked wherever the model misses it later. One rule per label.
 */
export function findRules(finds: readonly NerFind[], vault: Vault): Rule[] {
  const byLabel = new Map<string, Set<string>>()
  for (const f of [...vault.found(), ...finds]) {
    let set = byLabel.get(f.label)
    if (!set) byLabel.set(f.label, (set = new Set()))
    set.add(f.value)
  }
  // Emails first, so the names inside an address are taken with it.
  return [...byLabel]
    .sort(([a], [b]) => (a === 'EMAIL' ? -1 : b === 'EMAIL' ? 1 : 0))
    .map(([label, values]) => ({ label, scan: valueScanner(values), normalize: exactKey }))
}

/* ------------------------------------------------------------- placeholders */

/**
 * A placeholder as it appears in text the model reads: LABEL_n (or LABEL_n-k
 * for another spelling of the same value) and the session's marker after a
 * dot. Only placeholders with this session's marker are ever restored.
 */
const PLACEHOLDER = /<<([A-Z][A-Z0-9_]*_\d+(?:-\d+)?)\.([a-z0-9]{4})>>/g

/** A new per-session marker: four random lowercase letters or digits. */
export function newMarker(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
  const bytes = crypto.getRandomValues(new Uint8Array(4))
  return Array.from(bytes, b => alphabet[b % alphabet.length]).join('')
}

/** LABEL_n-k → { base: LABEL_n, label, n, k }; k is 1 for the first spelling. */
function parseToken(token: string): { base: string; label: string; n: number; k: number } {
  const m = /^(([A-Z][A-Z0-9_]*)_(\d+))(?:-(\d+))?$/.exec(token)
  if (!m) return { base: token, label: token, n: 0, k: 1 }
  return { base: m[1] ?? token, label: m[2] ?? token, n: Number(m[3]), k: m[4] ? Number(m[4]) : 1 }
}

/**
 * The two-way table between real values and placeholders, for one session.
 * A value is an entity (any case, any spacing) in one exact spelling; each
 * entity gets a number and each spelling its own token, so restoring gives
 * back exactly what was written (which Claude's Edit tool needs).
 */
export class Vault {
  private byForm = new Map<string, string>()
  private byEntity = new Map<string, string>()
  private byToken = new Map<string, VaultEntry>()
  private counters = new Map<string, number>()
  private variants = new Map<string, number>()
  readonly marker: string

  constructor(marker: string) {
    this.marker = marker
  }

  get size() {
    return this.byToken.size
  }

  /** The text that stands for `token` in this session. */
  format(token: string): string {
    return `<<${token}.${this.marker}>>`
  }

  /** The real value behind a placeholder, only when it carries this session's marker. */
  resolve(token: string, marker: string): string | undefined {
    return marker === this.marker ? this.byToken.get(token)?.value : undefined
  }

  tokenFor(label: string, value: string): string {
    const formKey = `${label}\u0000${value}`
    const known = this.byForm.get(formKey)
    if (known !== undefined) return known
    const entityKey = `${label}\u0000${exactKey(value)}`
    let token = this.byEntity.get(entityKey)
    if (token === undefined) {
      const n = (this.counters.get(label) ?? 0) + 1
      this.counters.set(label, n)
      token = `${label}_${n}`
      this.byEntity.set(entityKey, token)
    } else {
      const k = (this.variants.get(token) ?? 1) + 1
      this.variants.set(token, k)
      token = `${token}-${k}`
    }
    this.byForm.set(formKey, token)
    this.byToken.set(token, { token, value, label })
    return token
  }

  /** Marks placeholders already in a resumed conversation as taken. */
  reserve(text: string): void {
    for (const m of text.matchAll(PLACEHOLDER)) this.bump(parseToken(m[1] ?? ''))
  }

  private bump(t: { base: string; label: string; n: number; k: number }): void {
    if (t.n > (this.counters.get(t.label) ?? 0)) this.counters.set(t.label, t.n)
    if (t.k > (this.variants.get(t.base) ?? 1)) this.variants.set(t.base, t.k)
  }

  entries(): VaultEntry[] {
    return [...this.byToken.values()]
  }

  /** Every value found so far, to keep masking it where the model misses it. */
  found(): NerFind[] {
    return this.entries().map(e => ({ value: e.value, label: e.label }))
  }

  /** Restores entries saved earlier (before a hot reload, or beside the transcript). */
  load(entries: readonly VaultEntry[]) {
    for (const entry of entries) {
      if (this.byToken.has(entry.token)) continue
      const t = parseToken(entry.token)
      this.byForm.set(`${entry.label}\u0000${entry.value}`, entry.token)
      const entityKey = `${entry.label}\u0000${exactKey(entry.value)}`
      if (!this.byEntity.has(entityKey)) this.byEntity.set(entityKey, t.base)
      this.byToken.set(entry.token, entry)
      this.bump(t)
    }
  }
}

/** Applies `fn` to the parts of `text` that are not already placeholders. */
function outsidePlaceholders(text: string, fn: (plain: string) => string): string {
  let out = ''
  let last = 0
  for (const m of text.matchAll(PLACEHOLDER)) {
    out += fn(text.slice(last, m.index)) + m[0]
    last = m.index + m[0].length
  }
  return out + fn(text.slice(last))
}

/** Replaces every value the rules find with its placeholder. */
export function mask(text: string, rules: readonly Rule[], vault: Vault): string {
  let result = text
  for (const rule of rules) {
    result = outsidePlaceholders(result, plain => rule.scan(plain, m => vault.format(vault.tokenFor(rule.label, m))))
  }
  return result
}

/** Restores this session's placeholders. */
export function unmask(text: string, vault: Vault): string {
  return text.replace(PLACEHOLDER, (whole, token: string, marker: string) => vault.resolve(token, marker) ?? whole)
}

/** Unmasks every string inside a JSON-like value, leaving the listed top-level keys alone. */
export function unmaskDeep<T>(value: T, vault: Vault, skipKeys: ReadonlySet<string> = new Set()): T {
  const walk = (v: unknown, top: boolean): unknown => {
    if (typeof v === 'string') return unmask(v, vault)
    if (Array.isArray(v)) return v.map(x => walk(x, false))
    if (v !== null && typeof v === 'object') {
      const out: Record<string, unknown> = {}
      for (const [k, x] of Object.entries(v)) out[k] = top && skipKeys.has(k) ? x : walk(x, false)
      return out
    }
    return v
  }
  return walk(value, true) as T
}

/**
 * Unmasks a text stream whose pieces may split a placeholder ("<<PERS" +
 * "ON_1.k3f9>>"): holds back a tail that could still become one.
 */
export class StreamUnmasker {
  private pending = ''
  private readonly vault: Vault

  constructor(vault: Vault) {
    this.vault = vault
  }

  push(piece: string): string {
    this.pending += piece
    const hold = holdFrom(this.pending)
    const ready = this.pending.slice(0, hold)
    this.pending = this.pending.slice(hold)
    return unmask(ready, this.vault)
  }

  flush(): string {
    const rest = this.pending
    this.pending = ''
    return unmask(rest, this.vault)
  }
}

function holdFrom(buf: string): number {
  const i = buf.lastIndexOf('<<')
  if (i >= 0 && buf.length - i <= 80 && /^<<(?:[A-Z][A-Z0-9_]*(?:-\d*)?(?:\.[a-z0-9]{0,4})?)?>?$/.test(buf.slice(i))) return i
  if (buf.endsWith('<')) return buf.length - 1
  return buf.length
}

/** The note added to the system prompt, with examples in this session's form. */
export function systemNote(vault: Vault): string {
  return (
    `Personal data in this conversation (names, emails, phone numbers, addresses, ID and account numbers) has been ` +
    `replaced with placeholders such as ${vault.format('PERSON_1')} or ${vault.format('EMAIL_2')}. ` +
    `A placeholder with a -2, -3 ... after its number, such as ${vault.format('PERSON_1-2')}, is another spelling of the same value. ` +
    'Use a placeholder exactly as written, including the part after the dot, wherever its real value belongs ' +
    '(prose, code, commands, file edits): it is restored automatically before any tool runs and before the user ' +
    'reads your reply. Never guess, reconstruct or invent the real values, and do not comment on the placeholders unless asked.'
  )
}

/* ------------------------------------------------------------ local copies */

/** The vault file beside a session's transcript: `<session-id>.pii-mask.json`. */
export function vaultFileFor(transcriptPath: string): string {
  return `${transcriptPath.replace(/\.jsonl$/, '')}.pii-mask.json`
}

/** What the vault file holds. */
export type VaultFile = { version: 1; marker: string; entries: VaultEntry[] }

/** Reads a vault file's text; throws when it is not one. */
export function parseVaultFile(text: string): VaultFile {
  const data = JSON.parse(text) as Partial<VaultFile>
  if (data.version !== 1 || typeof data.marker !== 'string' || !Array.isArray(data.entries)) {
    throw new Error('not a pii-mask vault file (version 1)')
  }
  for (const e of data.entries) {
    if (typeof e?.token !== 'string' || typeof e.value !== 'string' || typeof e.label !== 'string') {
      throw new Error('a vault entry is malformed')
    }
  }
  return data as VaultFile
}

/** Replaces every value the vault knows with its placeholder, wherever it appears as a whole word. */
export function hideKnownValues(text: string, vault: Vault): string {
  const values = vault.entries().filter(e => e.value.trim().length > 1)
  if (values.length === 0) return text
  return mask(text, findRules([], vault), vault)
}

/** Keys in a tool's structured record that name a file or folder rather than hold content. */
const PATH_KEY = /path|cwd|dir/i

/** Top-level text fields of transcript records Claude Code writes without the mod seeing them. */
const RAW_TEXT_FIELDS = ['aiTitle', 'lastPrompt', 'summary']

/**
 * Masks the copies Claude Code keeps in a session's transcript outside the
 * rows the mod already masked: the queue record of each typed prompt, the
 * structured record of each tool result (`toolUseResult`), and generated
 * titles or summaries, with every value the vault knows. Paths are left
 * alone; lines that need no change are kept byte for byte.
 */
export function cleanTranscript(text: string, vault: Vault): { text: string; changedLines: number } {
  let changedLines = 0
  const hide = (v: string) => hideKnownValues(v, vault)
  const hideDeep = (v: unknown, key = ''): unknown => {
    if (typeof v === 'string') return PATH_KEY.test(key) ? v : hide(v)
    if (Array.isArray(v)) return v.map(x => hideDeep(x, key))
    if (v !== null && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, hideDeep(x, k)]))
    return v
  }
  const lines = text.split('\n').map(line => {
    if (!line.trim()) return line
    let record: Record<string, unknown>
    try {
      record = JSON.parse(line) as Record<string, unknown>
    } catch {
      return line
    }
    const out: Record<string, unknown> = { ...record }
    if (record.type === 'queue-operation' && typeof record.content === 'string') out.content = hide(record.content)
    for (const field of RAW_TEXT_FIELDS) if (typeof record[field] === 'string') out[field] = hide(record[field] as string)
    if ('toolUseResult' in record) out.toolUseResult = hideDeep(record.toolUseResult)
    const next = JSON.stringify(out)
    if (next === JSON.stringify(record)) return line
    changedLines += 1
    return next
  })
  return { text: lines.join('\n'), changedLines }
}
