import { usd, tokens } from './format'

function Table({ title, rows, labelKey, labelHeader, onRowClick }) {
  const total = rows.reduce((sum, r) => sum + (r.cost_usd || 0), 0)
  return (
    <section className="cost-table-block">
      <h4>{title}</h4>
      <table className="cost-table">
        <thead>
          <tr>
            <th>{labelHeader}</th><th>Sessions</th><th>Calls</th>
            <th>In</th><th>Out</th><th>Cache read</th><th>Cache write</th>
            <th>Cost</th><th>%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            // A pruned session keeps its spend but loses its sessions-table row,
            // so there is no drill-down to open -- don't offer a click that can
            // only 404.
            const clickable = Boolean(onRowClick && r[labelKey])
            return (
            <tr key={r.session_id ?? r[labelKey] ?? i}
                className={clickable ? 'clickable' : ''}
                onClick={clickable ? () => onRowClick(r) : undefined}>
              <td>{r[labelKey] || <em className="cost-note">(pruned)</em>}</td>
              <td>{r.sessions}</td>
              <td>{r.api_calls}</td>
              <td>{tokens(r.input_tokens)}</td>
              <td>{tokens(r.output_tokens)}</td>
              <td>{tokens(r.cache_read_tokens)}</td>
              <td>{tokens(r.cache_write_tokens)}</td>
              <td>{usd(r.cost_usd)}</td>
              <td>{total ? `${((r.cost_usd / total) * 100).toFixed(0)}%` : '—'}</td>
            </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

export default function CostTables({ byModel, byPlatform, bySession, onSelectSession }) {
  return (
    <div className="cost-tables">
      <Table title="By model" rows={byModel || []} labelKey="model" labelHeader="Model" />
      <Table title="By platform" rows={byPlatform || []} labelKey="platform" labelHeader="Platform" />
      <Table title="Top spenders" rows={bySession || []} labelKey="title" labelHeader="Session"
             onRowClick={(r) => onSelectSession(r.session_id)} />
    </div>
  )
}
