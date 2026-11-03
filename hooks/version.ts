/** True when version `a` is newer than `b` ('' is older than any). */
export function newerVersion(a: string, b: string): boolean {
  const pa = a.split('.').map(Number)
  const pb = b ? b.split('.').map(Number) : []
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0
    const y = pb[i] ?? -1
    if (x !== y) return x > y
  }
  return false
}
