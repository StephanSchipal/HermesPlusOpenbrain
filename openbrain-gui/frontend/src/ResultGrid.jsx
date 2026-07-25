import { formatDateTime } from './format.js'

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
            <div className="result-meta">
              {row.source_url && (
                <a href={row.source_url} target="_blank" rel="noopener noreferrer">
                  {row.source_url}
                </a>
              )}
            </div>
            <div className="result-meta">
              {formatDateTime(row.created_at)} · keywords: {row.keywords.join(', ')}
              {row.score != null && ` · relevance: ${Math.round(row.score * 100)}%`}
            </div>
          </div>
        </label>
      ))}
    </div>
  )
}
