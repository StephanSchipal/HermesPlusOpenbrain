import { usd, tokens, pct } from './format'

export default function CostSummary({ summary, unavailable, onExplain }) {
  if (unavailable) {
    return <p className="cost-unavailable">Hermes data is not available — {unavailable}</p>
  }
  if (!summary) return <p className="cost-loading">Loading…</p>

  const h = summary.hermes
  const cmp = summary.estimate_vs_actual
  const incomplete = summary.total_cost_of_ownership_incomplete
  // Hermes prices from a bundled table that does not cover every model it can
  // talk to, so some rows carry real tokens at zero cost. Say the total is a
  // lower bound rather than presenting it as complete.
  const unpriced = h.unpriced || { api_calls: 0, tokens: 0, models: [] }
  const hasUnpriced = unpriced.api_calls > 0

  return (
    <div className="cost-tiles">
      <div className="cost-tile">
        <span className="cost-tile-label">
          Total cost of ownership
          <button type="button" className="explain-button" onClick={onExplain}
                  title="Where do these numbers come from?">?</button>
        </span>
        <strong>
          {usd(summary.total_cost_of_ownership_usd)}
          {(incomplete || hasUnpriced) && <span className="cost-warning">*</span>}
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
        <strong>
          {hasUnpriced && <span className="cost-warning" title="lower bound">≥ </span>}
          {usd(h.cost_usd)}
        </strong>
        <span className="cost-tile-sub">
          {hasUnpriced ? (
            <button type="button" className="link-button cost-warning" onClick={onExplain}>
              {tokens(unpriced.tokens)} tokens unpriced — why?
            </button>
          ) : cmp
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
