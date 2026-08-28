import { useEffect, useState } from 'react'
import { api } from './api'
import { usd, tokens, pct } from './format'

// The merged "All" view only: a per-bot cost breakdown that doubles as a
// picker -- clicking a row drives the profile selector in CostView.
export default function CostByBot({ days, onPick }) {
  const [rows, setRows] = useState(null)

  useEffect(() => {
    api.getCostByBot(days).then(setRows).catch(() => setRows(null))
  }, [days])

  if (!rows || rows.length === 0) return null

  return (
    <section className="cost-table-block">
      <h4>Cost by bot</h4>
      <table className="cost-table">
        <thead>
          <tr>
            <th>Bot</th><th>Sessions</th><th>API calls</th>
            <th>Tokens</th><th>Cost</th><th>%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="clickable" onClick={() => onPick(r.key)}>
              <td>{r.label}{r.unavailable ? ' (unavailable)' : ''}</td>
              <td>{r.sessions}</td>
              <td>{r.api_calls}</td>
              <td>{tokens(r.tokens)}</td>
              <td>{usd(r.cost_usd)}</td>
              <td>{pct(r.pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
