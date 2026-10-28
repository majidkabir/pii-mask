// Built-in patterns for personal data with a known shape. They run before the
// model and catch what has a fixed form (emails, card numbers, IBANs, national
// ID numbers, keys); the model finds what has none (names, addresses).
// Where the data carries a checksum, a match must pass it, so random numbers,
// versions, timestamps and hashes are left alone. Pure: no `$`.

import type { Rule } from './masker'

const exactKey = (v: string) => v.toLowerCase().replace(/\s+/g, ' ')
const digitsOf = (v: string) => v.replace(/\D/g, '')

/* ------------------------------------------------------------- checksums */

/** Luhn (mod 10): card numbers, Swedish personal numbers. */
export function luhn(digits: string): boolean {
  let sum = 0
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i])
    if (i % 2 === 1) {
      d *= 2
      if (d > 9) d -= 9
    }
    sum += d
  }
  return digits.length > 0 && sum % 10 === 0
}

/** IBAN: ISO 13616 mod-97 check. */
export function ibanValid(iban: string): boolean {
  const s = iban.replace(/[\s-]/g, '').toUpperCase()
  if (!/^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/.test(s)) return false
  const moved = s.slice(4) + s.slice(0, 4)
  let rem = 0
  for (const ch of moved) {
    const part = /[A-Z]/.test(ch) ? String(ch.charCodeAt(0) - 55) : ch
    for (const d of part) rem = (rem * 10 + Number(d)) % 97
  }
  return rem === 1
}

/** A card number: 13–19 digits, a known network prefix, and Luhn. */
export function cardValid(v: string): boolean {
  const d = digitsOf(v)
  if (d.length < 13 || d.length > 19 || /^(\d)\1+$/.test(d)) return false
  if (!/^(4|5[1-5]|2[2-7]|3[47]|3[0689]|6(?:011|5|4[4-9]|22)|35)/.test(d)) return false
  return luhn(d)
}

const validDate = (dd: number, mm: number) => mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31

/** US Social Security number: no 000, 666 or 9xx area, no 00 group, no 0000 serial. */
export function ssnValid(v: string): boolean {
  const [a, g, s] = v.split('-')
  return !!a && !!g && !!s && a !== '000' && a !== '666' && a[0] !== '9' && g !== '00' && s !== '0000'
}

/** Danish CPR number: DDMMYY-NNNN with a real day and month. */
export function cprValid(v: string): boolean {
  const d = digitsOf(v)
  return d.length === 10 && validDate(Number(d.slice(0, 2)), Number(d.slice(2, 4)))
}

/** Swedish personal number: (YY)YYMMDD-NNNC, Luhn over the last ten digits. */
export function personnummerValid(v: string): boolean {
  const d = digitsOf(v).slice(-10)
  const dd = Number(d.slice(4, 6))
  return d.length === 10 && validDate(dd > 60 ? dd - 60 : dd, Number(d.slice(2, 4))) && luhn(d)
}

/** Norwegian national identity number: 11 digits, two mod-11 check digits. */
export function fodselsnummerValid(v: string): boolean {
  const d = digitsOf(v)
  if (d.length !== 11) return false
  const n = [...d].map(Number) as number[]
  const dd = n[0]! * 10 + n[1]!
  if (!validDate(dd > 40 ? dd - 40 : dd, n[2]! * 10 + n[3]!)) return false
  const check = (w: number[], len: number) => {
    const r = 11 - (w.reduce((s, x, i) => s + x * n[i]!, 0) % 11)
    return (r === 11 ? 0 : r) === n[len] && r !== 10
  }
  return check([3, 7, 6, 1, 8, 9, 4, 5, 2], 9) && check([5, 4, 3, 2, 7, 6, 5, 4, 3, 2], 10)
}

/** Finnish personal identity code: DDMMYY, century sign, NNN, check character. */
export function hetuValid(v: string): boolean {
  const m = /^(\d{6})[-+A-FU-Y](\d{3})([0-9A-Y])$/i.exec(v)
  if (!m) return false
  const d = m[1]!
  if (!validDate(Number(d.slice(0, 2)), Number(d.slice(2, 4)))) return false
  return '0123456789ABCDEFHJKLMNPRSTUVWXY'[Number(d + m[2]) % 31] === m[3]!.toUpperCase()
}

/* ------------------------------------------------------------- building */

type PatternRule = Rule & { name: string }

/** A rule from a regex: the whole match is the value, kept when `valid` says so. */
function whole(name: string, label: string, re: RegExp, valid: (v: string) => boolean = () => true): PatternRule {
  return {
    name,
    label,
    normalize: exactKey,
    scan: (text, onMatch) => text.replace(re, m => (valid(m) ? onMatch(m) : m)),
  }
}

/**
 * A rule with context: the regex's first group is the context (kept as it
 * is), the second the value (masked when `valid` says so). "Date of birth:
 * 12.03.1985" masks only the date.
 */
function after(name: string, label: string, re: RegExp, valid: (v: string) => boolean = () => true): PatternRule {
  return {
    name,
    label,
    normalize: exactKey,
    scan: (text, onMatch) => text.replace(re, (all, ctx: string, value: string) => (valid(value) ? ctx + onMatch(value) : all)),
  }
}

const MONTH = '(?:jan|feb|mar|apr|maj|may|jun|jul|aug|sep|okt|oct|nov|dec)[a-zé]*\\.?'
const DATE = `(?:\\d{1,4}[./-]\\d{1,2}[./-]\\d{1,4}|\\d{1,2}\\.?\\s+${MONTH}\\s+\\d{4}|${MONTH}\\s+\\d{1,2},?\\s+\\d{4})`

/** Code, not a secret: an identifier, a member chain or a call (`getPassword()`, `os.environ`). */
const looksLikeCode = (v: string) =>
  /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\(|\[|$)/.test(v) && !/\d/.test(v) || /^[{$<%(]/.test(v) || /^(?:null|none|true|false|undefined|\*+|x+|changeme|password|secret)$/i.test(v)

/**
 * The built-in patterns, in the order they run. Earlier ones win, so whole
 * keys and emails go before the shorter shapes that could match inside them,
 * and phone numbers, the loosest shape, go last.
 */
export const PATTERNS: readonly PatternRule[] = [
  // Passwords and keys
  whole('private key', 'SECRET', /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----/g),
  after('password in a URL', 'SECRET', /(:\/\/[^\s/:@]+:)([^\s/@]{3,})(?=@)/g),
  whole('AWS access key', 'SECRET', /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g),
  whole('GitHub token', 'SECRET', /\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b/g),
  whole('Slack token', 'SECRET', /\bxox[abeprs]-[A-Za-z0-9-]{10,}\b/g),
  whole('Stripe key', 'SECRET', /\b[rsp]k_(?:live|test)_[A-Za-z0-9]{20,}\b/g),
  whole('Google API key', 'SECRET', /\bAIza[0-9A-Za-z_-]{35}\b/g),
  whole('Anthropic or OpenAI key', 'SECRET', /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b/g),
  whole('JSON web token', 'SECRET', /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g),
  after('password or key in a setting', 'SECRET',
    /(\b(?:password|passwd|pwd|passphrase|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)["']?\s*[:=]\s*["']?)([^\s"',;]{6,})/gi,
    v => !looksLikeCode(v)),

  // Contact details and money
  whole('email', 'EMAIL', /(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])/g),
  whole('IBAN', 'IBAN', /\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b/g, ibanValid),
  whole('card number', 'CARD', /(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])/g, cardValid),

  // Values named by the words before them
  after('date of birth', 'BIRTHDATE',
    new RegExp(`(\\b(?:date of birth|birth ?date|d\\.?o\\.?b\\.?|born(?: on)?|birthday|fødselsdato|født(?: den)?|född|geboren(?: am)?|né(?:e)? le|fecha de nacimiento)\\s*[:\\-]?\\s*)(${DATE})`, 'gi')),
  after('passport number', 'PASSPORT', /(\b(?:passport|pasnummer|passnummer|reisepass)\s*(?:no\.?|nr\.?|number|nummer|#)?\s*[:#]?\s*(?:is\s+|er\s+|ist\s+|är\s+)?)([A-Z0-9]{6,9})\b/gi, v => /\d/.test(v)),
  after('account number', 'ACCOUNT',
    /(\b(?:account|acct\.?|bank account|kontonummer|kontonr\.?|konto|reg\.?\s?nr\.?)\s*(?:no\.?|nr\.?|number|#)?\s*[:#]?\s*)(\d[\d -]{4,24}\d)/gi,
    v => digitsOf(v).length >= 6),
  after('ID number named as one', 'ID_NUMBER',
    /(\b(?:ssn|social security(?: number| no\.?)?|national id(?: number)?|personal id(?: number)?|id[- ]?(?:number|no\.?|nr\.?)|tax id|tin|cpr(?:-?n(?:umme)?r\.?)?|personnummer|fødselsnummer|henkilötunnus|bsn|nif|dni|pesel|steuer-?id|personalausweis)\s*[:#]?\s*)([A-Z0-9][A-Z0-9.-]{3,18}[A-Z0-9]|\d{2,}(?: \d{2,}){1,5})/gi,
    v => digitsOf(v).length >= 5),

  // National ID numbers with a checksum or a fixed structure
  whole('US Social Security number', 'ID_NUMBER', /\b\d{3}-\d{2}-\d{4}\b/g, ssnValid),
  whole('Danish CPR number', 'ID_NUMBER', /\b\d{6}-\d{4}\b/g, cprValid),
  whole('Swedish personal number', 'ID_NUMBER', /\b(?:\d{2})?\d{6}[-+]\d{4}\b/g, personnummerValid),
  whole('Norwegian national ID', 'ID_NUMBER', /\b\d{6} ?\d{5}\b/g, fodselsnummerValid),
  whole('Finnish personal ID', 'ID_NUMBER', /\b\d{6}[-+A-FU-Y]\d{3}[0-9A-Y]\b/g, hetuValid),
  whole('UK National Insurance number', 'ID_NUMBER',
    /\b(?!BG|GB|KN|NK|NT|TN|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b/g),

  // Network and place
  whole('MAC address', 'MAC', /\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b|\b[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5}\b/g,
    v => !/^(?:00[:-]){5}00$|^(?:ff[:-]){5}ff$/i.test(v)),
  whole('IPv6 address', 'IP',
    /(?<![\w:.])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?)(?![\w:])/g,
    v => !v.includes('::') || (v.replace(/:/g, '').length >= 4 && v.split(':').filter(Boolean).length <= 7)),
  whole('IPv4 address', 'IP', /(?<![\w.]|\bv|==\s?|@)(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\w|\.\w)/g,
    v => !/^(?:0\.0\.0\.0|127\.\d+\.\d+\.\d+|255\.255\.255\.\d+|1\.0\.0\.0)$/.test(v)),
  whole('coordinates', 'LOCATION', /(?<![\d.])-?(?:[1-8]?\d|90)\.\d{4,},\s?-?(?:1[0-7]\d|[1-9]?\d|180)\.\d{4,}(?!\d|\.\d)/g),

  // Phone numbers, last: international (+ or 00), then common national forms
  whole('phone number', 'PHONE',
    /(?<![\w+])(?:\+|00)[1-9]\d{0,2}[\s.-]?(?:\(\d{1,4}\)[\s.-]?)?\d{1,4}(?:[\s.-]?\d{1,4}){1,5}(?![\w.-]?\d)/g,
    v => { const n = digitsOf(v).length - (v.startsWith('00') ? 2 : 0); return n >= 8 && n <= 15 }),
  whole('phone number (national)', 'PHONE',
    /\(\d{3}\)\s?\d{3}-\d{4}\b|\b\d{3}-\d{3}-\d{4}\b|\b0\d{1,4}[\s-]\d{3,4}[\s-]?\d{3,4}\b|(?<!\d )\b\d{2} \d{2} \d{2} \d{2}\b(?! \d)|\b07\d{3}\s?\d{6}\b/g),
]
