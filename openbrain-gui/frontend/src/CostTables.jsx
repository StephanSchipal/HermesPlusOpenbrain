import { usd, tokens } from './format'

const SUM_KEYS = ['sessions', 'api_calls', 'input_tokens', 'output_tokens',
                   'cache_read_tokens', 'cache_write_tokens', 'cost_usd']

// An unpriced model (Hermes has no rate for it, so its own cost_usd reads 0 --
// see costpage.md §2.6) may still have a real dollar figure the user tracked
// by hand as an External-costs row named after the model. This is a
// cross-reference note only -- it never changes cost_usd or any total, so it
// can't create a double-count against the External-costs total or the header
// Total-cost-of-ownership tile, which already counts that row once.
function matchExternalCost(modelName, externalCosts) {
  if (!modelName || !externalCosts?.length) return null
  const needle = modelName.trim().toLowerCase()
  return externalCosts.find((r) => (r.name || '').trim().toLowerCase() === needle
    && r.usd != null) || null
}

function Table({ title, rows, labelKey, labelHeader, onRowClick, botColumn, externalCosts }) {
  const total = rows.reduce((sum, r) => sum + (r.cost_usd || 0), 0)
  // Gate the Bot column on the data actually carrying `profile`, not just the
  // caller's flag: a single-bot payload (or a pre-feature saved report) has no
  // `profile` on its rows, so the column would be all "—".
  const showBot = botColumn && rows.some((r) => r.profile)

  // A pruned session keeps its spend but loses its sessions-table row, so
  // there is no label and no drill-down to open. A long tail of these used to
  // push real rows off screen one at a time; fold them into a single summary
  // row at the end instead.
  const named = rows.filter((r) => r[labelKey])
  const pruned = rows.filter((r) => !r[labelKey])
  const displayRows = pruned.length
    ? [...named, {
        ...Object.fromEntries(SUM_KEYS.map((k) => [k, pruned.reduce((s, r) => s + (r[k] || 0), 0)])),
        _prunedCount: pruned.length,
      }]
    : rows

  return (
    <section className="cost-table-block">
      <h4>{title}</h4>
      <table className="cost-table">
        <thead>
          <tr>
            {showBot && <th>Bot</th>}
            <th>{labelHeader}</th><th>Sessions</th><th>Calls</th>
            <th>In</th><th>Out</th><th>Cache read</th><th>Cache write</th>
            <th>Cost</th><th>%</th>
          </tr>
        </thead>
        <tbody>
          {displayRows.map((r, i) => {
            const clickable = Boolean(onRowClick && r[labelKey])
            const externalMatch = !r.cost_usd && labelKey === 'model'
              ? matchExternalCost(r[labelKey], externalCosts) : null
            return (
            <tr key={r.session_id ?? r[labelKey] ?? i}
                className={clickable ? 'clickable' : ''}
                onClick={clickable ? () => onRowClick(r) : undefined}>
              {showBot && <td>{r.profile || '—'}</td>}
              <td>{r[labelKey] || <em className="cost-note">(pruned {r._prunedCount})</em>}</td>
              <td>{r.sessions}</td>
              <td>{r.api_calls}</td>
              <td>{tokens(r.input_tokens)}</td>
              <td>{tokens(r.output_tokens)}</td>
              <td>{tokens(r.cache_read_tokens)}</td>
              <td>{tokens(r.cache_write_tokens)}</td>
              <td>
                {usd(r.cost_usd)}
                {externalMatch && (
                  <em className="cost-note"
                      title={`Not priced by Hermes (counted as $0 above) -- tracked by hand in External costs as "${externalMatch.name}"`}>
                    {' '}(see External costs: {usd(externalMatch.usd)})
                  </em>
                )}
              </td>
              <td>{total ? `${((r.cost_usd / total) * 100).toFixed(0)}%` : '—'}</td>
            </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

export default function CostTables({ byModel, byPlatform, bySession, profile, onSelectSession, externalCosts }) {
  return (
    <div className="cost-tables">
      <Table title="By model" rows={byModel || []} labelKey="model" labelHeader="Model"
             externalCosts={externalCosts} />
      <Table title="By platform" rows={byPlatform || []} labelKey="platform" labelHeader="Platform" />
      <Table title="Top spenders" rows={bySession || []} labelKey="title" labelHeader="Session"
             botColumn={profile === 'all'}
             onRowClick={(r) => onSelectSession({ id: r.session_id, profile: r.profile || profile })} />
    </div>
  )
}
