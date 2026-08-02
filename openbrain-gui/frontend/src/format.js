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

function ddmmyyyy(d) {
  const dd = String(d.getDate()).padStart(2, '0')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  return `${dd}.${mm}.${d.getFullYear()}`
}

function rangeDates(days) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - days)
  return { start, end }
}

// "Today" is a single date; every other range is literally today minus the
// day count, dashed to today -- matching how the range buttons already
// select a rolling window ending now, not a calendar-aligned one.
export function dateRangeLabel(days, isToday) {
  const { start, end } = rangeDates(days)
  return isToday ? ddmmyyyy(end) : `${ddmmyyyy(start)} - ${ddmmyyyy(end)}`
}

// Same date math as dateRangeLabel, formatted for a filename: no spaces
// around the dash, and the day count zero-padded into the name so reports
// for different ranges sort and scan together, e.g. CostReport_07_20.06.2026-27.06.2026.
export function reportName(days, isToday) {
  const { start, end } = rangeDates(days)
  const code = String(days).padStart(2, '0')
  const datePart = isToday ? ddmmyyyy(end) : `${ddmmyyyy(start)}-${ddmmyyyy(end)}`
  return `CostReport_${code}_${datePart}`
}
