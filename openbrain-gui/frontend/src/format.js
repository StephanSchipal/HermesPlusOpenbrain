export function formatDateTime(iso) {
  if (!iso) return iso
  return iso.replace('T', ' ').slice(0, 19)
}

export function usd(value) {
  return value == null ? '—' : `$${Number(value).toFixed(2)}`
}

export function tokens(value) {
  if (value == null) return '—'
  const n = Number(value)
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
}

export function pct(value) {
  return value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`
}
