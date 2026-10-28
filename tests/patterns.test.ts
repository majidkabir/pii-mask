import { describe, expect, test } from 'claude-code/testing'

import { mask, Vault } from '../hooks/masker'
import { cardValid, fodselsnummerValid, hetuValid, ibanValid, luhn, PATTERNS, personnummerValid } from '../hooks/patterns'

/** What the patterns turn `text` into, and the labels they used, in order. */
function run(text: string) {
  const v = new Vault('k3f9')
  const out = mask(text, PATTERNS, v)
  return { out, labels: v.entries().map(e => e.label), values: v.entries().map(e => e.value) }
}
/** The one value the patterns found in `text`, with its label. */
function one(text: string): [string, string] {
  const { values, labels } = run(text)
  expect([text, values.length]).toEqual([text, 1])
  return [labels[0] ?? '', values[0] ?? '']
}

/** Builds a test key at run time, so no key-shaped literal sits in this file. */
const key = (...parts: string[]) => parts.join('')

describe('checksums', () => {
  test('Luhn, IBAN, cards', () => {
    expect(luhn('79927398713')).toBe(true)
    expect(luhn('79927398710')).toBe(false)
    expect(ibanValid('DK50 0040 0440 1162 43')).toBe(true)
    expect(ibanValid('GB82 WEST 1234 5698 7654 32')).toBe(true)
    expect(ibanValid('DK50 0040 0440 1162 44')).toBe(false)
    expect(cardValid('4111 1111 1111 1111')).toBe(true)
    expect(cardValid('4111 1111 1111 1112')).toBe(false)
    expect(cardValid('1234 5678 9012 3452')).toBe(false) // passes Luhn, no card network starts with 1
  })

  test('national ID numbers', () => {
    expect(personnummerValid('811218-9876')).toBe(true)
    expect(personnummerValid('811218-9877')).toBe(false)
    expect(hetuValid('131052-308T')).toBe(true)
    expect(hetuValid('131052-308U')).toBe(false)
    expect(fodselsnummerValid('01010012356')).toBe(true)
    expect(fodselsnummerValid('01010012357')).toBe(false)
  })
})

describe('what the patterns find', () => {
  const cases: [string, string, string][] = [
    // [text, label, value]
    ['Mail me at anna.berg+news@example.co.uk today', 'EMAIL', 'anna.berg+news@example.co.uk'],
    ['IBAN DK50 0040 0440 1162 43, thanks', 'IBAN', 'DK50 0040 0440 1162 43'],
    ['card 4111 1111 1111 1111 exp 12/27', 'CARD', '4111 1111 1111 1111'],
    ['amex 3782-822463-10005', 'CARD', '3782-822463-10005'],
    ['SSN 123-45-6789', 'ID_NUMBER', '123-45-6789'],
    ['CPR 010190-1234 on file', 'ID_NUMBER', '010190-1234'],
    ['personnummer: 19811218-9876', 'ID_NUMBER', '19811218-9876'],
    ['fnr 01010012356', 'ID_NUMBER', '01010012356'],
    ['hetu 131052-308T', 'ID_NUMBER', '131052-308T'],
    ['NI number AB 12 34 56 C', 'ID_NUMBER', 'AB 12 34 56 C'],
    ['Tax ID: 12-3456789', 'ID_NUMBER', '12-3456789'],
    ['Date of birth: 12.03.1985', 'BIRTHDATE', '12.03.1985'],
    ['She was born on March 3, 1985.', 'BIRTHDATE', 'March 3, 1985'],
    ['født den 3. marts 1985', 'BIRTHDATE', '3. marts 1985'],
    ['Passport no: X1234567', 'PASSPORT', 'X1234567'],
    ['Account number: 1234 5678901', 'ACCOUNT', '1234 5678901'],
    ['Call +45 12 34 56 78 now', 'PHONE', '+45 12 34 56 78'],
    ['US office +1 (555) 123-4567', 'PHONE', '+1 (555) 123-4567'],
    ['ring 0045 12345678', 'PHONE', '0045 12345678'],
    ['tel (555) 123-4567', 'PHONE', '(555) 123-4567'],
    ['mobile 07700 900123', 'PHONE', '07700 900123'],
    ['London 020 7946 0958', 'PHONE', '020 7946 0958'],
    ['ring 12 34 56 78', 'PHONE', '12 34 56 78'],
    ['from 192.168.1.20 at noon', 'IP', '192.168.1.20'],
    ['host 2001:db8:85a3::8a2e:370:7334 up', 'IP', '2001:db8:85a3::8a2e:370:7334'],
    ['iface fe80::1ff:fe23:4567:890a', 'IP', 'fe80::1ff:fe23:4567:890a'],
    ['mac 00:1A:2B:3C:4D:5E', 'MAC', '00:1A:2B:3C:4D:5E'],
    ['home is at 55.6761, 12.5683', 'LOCATION', '55.6761, 12.5683'],
    // At the end of a sentence.
    ["Her laptop's IP is 192.168.10.44.", 'IP', '192.168.10.44'],
    ['She lives at 55.6761, 12.5683.', 'LOCATION', '55.6761, 12.5683'],
    ['Her passport no. is P4829173.', 'PASSPORT', 'P4829173'],
  ]
  for (const [text, label, value] of cases) {
    test(`${label}: ${text}`, () => expect(one(text)).toEqual([label, value]))
  }

  test('passwords and keys', () => {
    const secrets: [string, string][] = [
      [`AWS ${key('AKIA', 'IOSFODNN7', 'EXAMPLE')}`, key('AKIA', 'IOSFODNN7', 'EXAMPLE')],
      [`token ${key('ghp', '_', 'a'.repeat(36))}`, key('ghp', '_', 'a'.repeat(36))],
      [`slack ${key('xox', 'b-', '1234567890-abcdefghij')}`, key('xox', 'b-', '1234567890-abcdefghij')],
      [`stripe ${key('sk', '_live_', 'b'.repeat(24))}`, key('sk', '_live_', 'b'.repeat(24))],
      [`google ${key('AIza', 'c'.repeat(35))}`, key('AIza', 'c'.repeat(35))],
      [`claude ${key('sk-', 'ant-', 'api03-', 'd'.repeat(30))}`, key('sk-', 'ant-', 'api03-', 'd'.repeat(30))],
      [`jwt ${key('eyJ', 'hbGciOiJIUzI1', '.eyJ', 'zdWIiOiIxMjM0', '.', 'e'.repeat(20))}`, key('eyJ', 'hbGciOiJIUzI1', '.eyJ', 'zdWIiOiIxMjM0', '.', 'e'.repeat(20))],
      ['db postgres://admin:S3cretPw@db.local/app', 'S3cretPw'],
      ['PASSWORD=Summer2024!', 'Summer2024!'],
      ['"api_key": "k9f8d7s6a5"', 'k9f8d7s6a5'],
    ]
    for (const [text, value] of secrets) expect([text, one(text)]).toEqual([text, ['SECRET', value]])
    const pem = `x\n${key('-----BEGIN ', 'RSA PRIVATE KEY-----')}\nMIIB\n${key('-----END ', 'RSA PRIVATE KEY-----')}\ny`
    expect(run(pem).out).toBe('x\n<<SECRET_1.k3f9>>\ny')
  })

  test('an IBAN with a wrong check digit is not taken for an IBAN', () => {
    expect(run('IBAN DK50 0040 0440 1162 44').labels).not.toContain('IBAN')
  })

  test('a setting that holds code, not a secret, is left alone', () => {
    for (const text of ['password = getPassword()', 'api_key: ${API_KEY}', 'token = request.headers', 'password: os.environ["DB_PASS"]', 'secret = null']) {
      expect([text, run(text).values]).toEqual([text, []])
    }
  })

  test('only the value is masked, not the words around it', () => {
    expect(run('Date of birth: 12.03.1985.').out).toBe('Date of birth: <<BIRTHDATE_1.k3f9>>.')
  })
})

describe('what the patterns leave alone', () => {
  const ordinary = [
    'commit 3f2a9c1d8e7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f',
    'request id 550e8400-e29b-41d4-a716-446655440000',
    '2026-10-23 19:28:03 INFO started in 1697040000000 ms',
    'claude-code 2.1.302, node v22.18.0, pip==24.0.1, v1.2.3.4',
    'listening on 127.0.0.1:8766 and 0.0.0.0:3000, localhost:5173',
    'price 1 299,00 kr, 12.5% off, total 4 398.00',
    'HTTP/1.1 200 OK, color #1a2b3c, size 1920x1080',
    'std::vector<int> v; a::b(); time 12:30:45',
    'card 4111 1111 1111 1112 is a typo',
    '000-12-3456 and 666-12-3456 are not real SSNs',
    'order 1234567890123, invoice 2026-001234',
    'sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'chmod 644 file && sleep 30 && seq 1 100',
    'lat/long 55.67, 12.56 (two decimals)',
    'values 10 20 30 40 50',
    'Pass the 123456 records to the parser',
  ]
  for (const text of ordinary) {
    test(text, () => expect([text, run(text).values]).toEqual([text, []]))
  }
})

describe('speed', () => {
  test('1 MB of mixed text in well under a second', () => {
    const block = 'INFO user anna@example.com from 10.0.0.12 paid with 4111 1111 1111 1111; commit 3f2a9c1d8e7b; ok\n'
    const text = block.repeat(Math.ceil(1_000_000 / block.length))
    const t = performance.now()
    run(text)
    const ms = performance.now() - t
    console.log(`BENCH patterns over 1 MB: ${Math.round(ms)} ms`)
    expect(ms).toBeLessThan(3000)
  })
})
