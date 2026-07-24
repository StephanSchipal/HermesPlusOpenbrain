export default function ResultGrid({ rows, selectedId, onSelect }) {
  if (rows.length === 0) {
    return <p className="grid-empty">No results yet — run a search.</p>
  }
  return (
    <div className="result-grid">
      {rows.map((row) => (
        <label key={row.id} className="result-row">
          <input
            type="radio"
            name="result-row"
            checked={selectedId === row.id}
            onChange={() => onSelect(row.id)}
          />
          <span className="result-id">{row.id.slice(0, 8)}</span>
          <div className="result-body">
            <div className="result-subject">{row.subject_line}</div>
            <div className="result-meta">{row.source_url}</div>
            <div className="result-meta">
              {row.created_at} · keywords: {row.keywords.join(', ')}
            </div>
          </div>
        </label>
      ))}
    </div>
  )
}
