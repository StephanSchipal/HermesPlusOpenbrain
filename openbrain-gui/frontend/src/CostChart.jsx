import { usd } from './format'

const PALETTE = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#b07aa1']
const WIDTH = 720
const HEIGHT = 220
const PAD = { top: 10, right: 10, bottom: 28, left: 48 }

export default function CostChart({ series, group, onGroupChange }) {
  if (!series) return <p className="cost-loading">Loading…</p>

  const days = [...new Set(series.points.map((p) => p.day))].sort()
  const groups = [...new Set(series.points.map((p) => p.group))].sort()

  // The ledger only knows what it has sampled since it was installed. Say that
  // plainly rather than draw an empty axis that reads as "you spent nothing".
  if (!days.length) {
    return (
      <section className="cost-table-block">
        <h4>Spend over time</h4>
        <p className="cost-note">
          {series.collecting_since
            ? `Collecting since ${series.collecting_since} — no completed intervals yet.`
            : 'Collecting from now — the chart fills in as the poller records activity. '
              + 'Hermes stores only lifetime totals per session, so history before '
              + 'this page existed cannot be reconstructed.'}
        </p>
      </section>
    )
  }

  const byDay = new Map(days.map((d) => [d, new Map()]))
  for (const p of series.points) byDay.get(p.day).set(p.group, p.cost_usd || 0)

  const dayTotals = days.map((d) => [...byDay.get(d).values()].reduce((a, b) => a + b, 0))
  const peak = Math.max(...dayTotals, 0.01)
  // Headroom so the tallest bar doesn't touch the top edge, and so the value
  // label printed above it has somewhere to sit.
  const maxTotal = peak * 1.15

  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  // Cap the slot so a handful of days don't stretch into slabs spanning the
  // whole plot. Bars then grow leftwards from the axis at a readable width and
  // the chart looks the same shape on day 1 as on day 30.
  const slot = Math.min(plotW / days.length, 56)
  const barW = Math.max(2, Math.min(slot * 0.7, 40))
  const labelEvery = Math.ceil(days.length / 8)
  // With only a few bars there is room to print the figure above each one --
  // far more legible than reading it off the axis.
  const showValues = days.length <= 10

  return (
    <section className="cost-table-block">
      <div className="cost-chart-header">
        <h4>Spend over time</h4>
        <div className="cost-chart-toggle">
          {['model', 'platform'].map((g) => (
            <button key={g} className={group === g ? 'active' : ''} onClick={() => onGroupChange(g)}>
              by {g}
            </button>
          ))}
        </div>
      </div>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="cost-chart" role="img"
           aria-label="Daily spend, stacked by group">
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + plotH} className="axis" />
        <line x1={PAD.left} y1={PAD.top + plotH} x2={PAD.left + plotW} y2={PAD.top + plotH} className="axis" />
        {/* Sits at the height the peak actually reaches, not at the top of the
            axis -- the axis carries 15% headroom above it. */}
        <text x={PAD.left - 6} y={PAD.top + plotH - (peak / maxTotal) * plotH + 4}
              className="tick" textAnchor="end">{usd(peak)}</text>
        <text x={PAD.left - 6} y={PAD.top + plotH} className="tick" textAnchor="end">$0</text>

        {days.map((day, i) => {
          const x = PAD.left + slot * i + (slot - barW) / 2
          const dayTotal = dayTotals[i]
          let cursor = PAD.top + plotH
          return (
            <g key={day}>
              {showValues && dayTotal > 0 && (
                <text x={x + barW / 2} y={PAD.top + plotH - (dayTotal / maxTotal) * plotH - 4}
                      className="tick" textAnchor="middle">
                  {usd(dayTotal)}
                </text>
              )}
              {groups.map((g, gi) => {
                const value = byDay.get(day).get(g) || 0
                if (!value) return null
                const h = (value / maxTotal) * plotH
                cursor -= h
                return (
                  <rect key={g} x={x} y={cursor} width={barW} height={h}
                        fill={PALETTE[gi % PALETTE.length]}>
                    <title>{`${day} · ${g} · ${usd(value)}`}</title>
                  </rect>
                )
              })}
              {i % labelEvery === 0 && (
                <text x={x + barW / 2} y={HEIGHT - 8} className="tick" textAnchor="middle">
                  {day.slice(5)}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      <div className="cost-chart-legend">
        {groups.map((g, gi) => (
          <span key={g}>
            <i style={{ background: PALETTE[gi % PALETTE.length] }} /> {g || '(pruned)'}
          </span>
        ))}
      </div>

      {days.length < 3 && (
        <p className="cost-note">
          {days.length} day{days.length === 1 ? '' : 's'} recorded so far, since{' '}
          {series.collecting_since}. Hermes stores only lifetime totals per session, so earlier
          days cannot be reconstructed — the chart fills out from here.
        </p>
      )}
    </section>
  )
}
