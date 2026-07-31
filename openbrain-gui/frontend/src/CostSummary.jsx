import { usd, tokens, pct } from './format'

export default function CostSummary({ summary, unavailable }) {
  if (unavailable) {
    return <p className="cost-unavailable">Hermes data is not available — {unavailable}</p>
  }
  if (!summary) return <p className="cost-loading">Loading…</p>

  const h = summary.hermes
  const cmp = summary.estimate_vs_actual
  const incomplete = summary.total_cost_of_ownership_incomplete

  return (
    <div className="cost-tiles">
      <div className="cost-tile">
        <span className="cost-tile-label">Total cost of ownership</span>
        <strong>
          {usd(summary.total_cost_of_ownership_usd)}
          {incomplete && <span className="cost-warning">*</span>}
        </strong>
        <span className="cost-tile-sub">
          {incomplete
            ? 'understated — a euro row needs an exchange rate'
            : (summary.total_cost_of_ownership_eur != null
                ? `€${summary.total_cost_of_ownership_eur.toFixed(2)}`
                : 'no rate set')}
          {summary.external.onetime_usd > 0 &&
            ` · one-off ${usd(summary.external.onetime_usd)}`}
        </span>
      </div>

      <div className="cost-tile">
        <span className="cost-tile-label">Hermes API cost <em>estimated</em></span>
        <strong>{usd(h.cost_usd)}</strong>
        <span className="cost-tile-sub">
          {cmp
            ? `${cmp.name} invoice ${usd(cmp.actual_usd)} · ${cmp.delta_pct >= 0 ? '+' : ''}${cmp.delta_pct.toFixed(1)}% vs 30d estimate`
            : `${summary.days} days`}
        </span>
      </div>

      <div className="cost-tile">
        <span className="cost-tile-label">API calls</span>
        <strong>{h.api_calls}</strong>
        <span className="cost-tile-sub">
          {tokens(h.cache_read_tokens + h.cache_write_tokens + h.input_tokens + h.output_tokens)} tokens
        </span>
      </div>

      <div className="cost-tile">
        <span className="cost-tile-label">Cache hit rate</span>
        <strong>{pct(h.cache_hit_rate)}</strong>
        <span className="cost-tile-sub">
          {tokens(h.cache_write_tokens)} written · a write costs 12.5× a read
        </span>
      </div>
    </div>
  )
}
