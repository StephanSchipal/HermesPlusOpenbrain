import { usd, tokens } from './format'

const SEGMENTS = [
  { key: 'cache_read_tokens', label: 'cache read', color: '#76b7b2' },
  { key: 'cache_write_tokens', label: 'cache write', color: '#e15759' },
  { key: 'input_tokens', label: 'input', color: '#4e79a7' },
  { key: 'output_tokens', label: 'output', color: '#f28e2b' },
]

function Composition({ hermes }) {
  const total = SEGMENTS.reduce((sum, s) => sum + (hermes[s.key] || 0), 0)
  if (!total) return <p className="cost-note">No token activity in this window.</p>

  let offset = 0
  return (
    <>
      <svg viewBox="0 0 100 6" className="composition-bar" preserveAspectRatio="none"
           role="img" aria-label="Token composition">
        {SEGMENTS.map((s) => {
          const width = ((hermes[s.key] || 0) / total) * 100
          const x = offset
          offset += width
          return (
            <rect key={s.key} x={x} y={0} width={width} height={6} fill={s.color}>
              <title>{`${s.label}: ${tokens(hermes[s.key])} (${width.toFixed(1)}%)`}</title>
            </rect>
          )
        })}
      </svg>
      <div className="cost-chart-legend">
        {SEGMENTS.map((s) => (
          <span key={s.key}>
            <i style={{ background: s.color }} /> {s.label} {tokens(hermes[s.key])}
          </span>
        ))}
      </div>
      <p className="cost-note">
        A cache <strong>write</strong> costs 12.5× a cache read — the red segment is where the
        money goes, not the green one.
      </p>
    </>
  )
}

export default function CostEfficiency({ hermes, efficiency }) {
  return (
    <div className="cost-efficiency">
      <section className="cost-table-block">
        <h4>Token composition</h4>
        {hermes ? <Composition hermes={hermes} /> : <p className="cost-loading">Loading…</p>}
      </section>

      <section className="cost-table-block">
        <h4>Efficiency</h4>
        <table className="cost-table">
          <thead>
            <tr>
              <th>Platform</th><th>Calls</th><th>Tokens / call</th>
              <th>Cache write / call</th><th>Cost / call</th><th>Msgs / session</th>
            </tr>
          </thead>
          <tbody>
            {(efficiency || []).map((r, i) => (
              <tr key={r.platform || i}>
                <td>{r.platform || <em className="cost-note">(pruned)</em>}</td>
                <td>{r.api_calls}</td>
                <td>{tokens(r.tokens_per_call)}</td>
                <td>{tokens(r.cache_write_per_call)}</td>
                <td>{usd(r.cost_per_call)}</td>
                <td>{r.avg_messages_per_session == null
                      ? '—' : Math.round(r.avg_messages_per_session)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
