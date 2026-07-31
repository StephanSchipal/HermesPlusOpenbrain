import { usd, tokens } from './format'

// Kept in sync with costpage.md §2.6 -- if you change one, change both.
export default function CostExplainPopup({ summary, onClose }) {
  const h = summary.hermes
  const unpriced = h.unpriced || { api_calls: 0, tokens: 0, models: [] }
  const hasUnpriced = unpriced.api_calls > 0

  return (
    <div className="popup-overlay">
      <div className="popup">
        <div className="popup-header-row">
          <h3>Where these numbers come from</h3>
        </div>

        <div className="popup-explain">
          <p>
            Nothing on this page is calculated from token counts. The cost is a straight sum of a
            column <strong>Hermes itself writes</strong>:
          </p>

          <pre className="explain-chain">
{`total cost of ownership   = ${usd(summary.total_cost_of_ownership_usd)}
  Hermes API estimate     = ${usd(h.cost_usd)}
  + external monthly      = ${usd(summary.external.monthly_usd)}

Hermes API estimate = SUM(estimated_cost_usd)
  FROM session_model_usage
  WHERE last_seen within ${summary.days} days`}
          </pre>

          <p>
            Hermes computes <code>estimated_cost_usd</code> per session and model from a pricing
            table bundled with the agent — <code>cost_source: official_docs_snapshot</code>,
            <code> cost_status: estimated</code>. It is <strong>not</strong> your invoice. Hermes
            has an <code>actual_cost_usd</code> column but never fills it in, which is exactly why
            this page lets you tick <strong>≈</strong> on the row holding your real bill.
          </p>

          <h4>A session is attributed to its last day</h4>
          <p>
            Hermes stores one row per session covering its <em>entire life</em>, so a thread
            running across six days cannot be split across them. A row counts in full when its
            last activity falls inside the selected range. Totals stay exact; splitting them
            proportionally would invent numbers the source does not contain.
          </p>

          <h4>Daily history starts when this page was installed</h4>
          <p>
            For the same reason, the chart cannot be reconstructed backwards. A poller samples the
            counters every five minutes and records the difference. Its first run deliberately
            writes nothing — otherwise day one would show one fabricated spike containing all of
            Hermes' prior history.
          </p>

          <h4>Tokens cannot be attributed to individual tools</h4>
          <p>
            Hermes records no per-message token counts, so the tools panel shows call counts only.
          </p>

          {hasUnpriced ? (
            <>
              <h4 className="cost-warning">This total is a lower bound</h4>
              <p>
                Hermes' pricing table has no entry for every model it can talk to. In the selected
                range <strong>{unpriced.api_calls} API calls</strong> carrying{' '}
                <strong>{tokens(unpriced.tokens)} tokens</strong> are priced at zero:
              </p>
              <ul>
                {unpriced.models.map((m) => <li key={m}><code>{m}</code></li>)}
              </ul>
              <p>
                Some of that may genuinely be free — a self-hosted model costs nothing per token.
                The rest is simply unknown. Either way the real figure is <em>at least</em>{' '}
                {usd(h.cost_usd)}, not exactly it.
              </p>
            </>
          ) : (
            <p>
              Every row in this range carries a price, so nothing is silently counted as free.
            </p>
          )}
        </div>

        <div className="popup-actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
