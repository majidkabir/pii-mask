// Speed of the masking code itself, without the model: the built-in patterns
// plus the values found so far (the vault), on text of growing size, and
// putting the real values back (unmask). This runs in the hook on every row.
//   bun mask_speed.ts
import { findRules, mask, unmask, Vault } from '../../hooks/masker'
import { PATTERNS } from '../../hooks/patterns'

const block = 'INFO 2026-11-03 order 48213 for Anna Berg (anna.berg@example.com, +45 28 47 19 63) paid with 4111 1111 1111 1111 from 10.0.0.12; commit 3f2a9c1d8e7b ok\n'
const names = (n: number) => Array.from({ length: n }, (_, i) => ({ value: `Person${i} Surname${i}`, label: 'PERSON' }))

function best(fn: () => void, runs = 3): number {
  let min = Infinity
  for (let i = 0; i < runs; i++) {
    const t = performance.now()
    fn()
    min = Math.min(min, performance.now() - t)
  }
  return min
}

console.log('| Text | Values already found | Mask, ms | Unmask, ms |')
console.log('| --- | --- | --- | --- |')
for (const size of [1_000, 10_000, 100_000, 1_000_000]) {
  for (const found of [10, 1_000]) {
    const text = block.repeat(Math.ceil(size / block.length)).slice(0, size)
    let masked = ''
    const v = new Vault('perf')
    const rules = [...findRules(names(found), v), ...PATTERNS, ...findRules([{ value: 'Anna Berg', label: 'PERSON' }], v)]
    const m = best(() => { masked = mask(text, rules, v) })
    const u = best(() => { unmask(masked, v) })
    console.log(`| ${size.toLocaleString('en')} chars | ${found.toLocaleString('en')} | ${m.toFixed(1)} | ${u.toFixed(1)} |`)
  }
}
