import { useEffect, useMemo, useState } from 'react'
import { formatDateTime } from './format.js'

function sortRows(rows, sortBy) {
  if (sortBy === 'date_desc') return [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at))
  if (sortBy === 'date_asc') return [...rows].sort((a, b) => a.created_at.localeCompare(b.created_at))
  return rows // 'relevance' -- already ordered by the backend
}

export default function ResultGrid({
  rows, selectedId, onSelect, onFindSimilar, hitCountLabel, hasSearched, resultsVersion,
}) {
  const hasRelevance = rows.length > 0 && rows[0].score != null
  const [sortBy, setSortBy] = useState(hasRelevance ? 'relevance' : 'date_desc')

  useEffect(() => {
    setSortBy(hasRelevance ? 'relevance' : 'date_desc')
    // Keyed on resultsVersion, not `rows` -- `rows` also gets a new array
    // reference from in-place edits (delete/change a row in the current
    // result set), which must NOT reset the user's chosen sort order. Only a
    // genuinely new search/browse/find-similar bumps resultsVersion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultsVersion])

  const sortedRows = useMemo(() => sortRows(rows, sortBy), [rows, sortBy])

  if (rows.length === 0 && !hasSearched) {
    return <p className="grid-empty">No results yet — run a search.</p>
  }

  return (
    <div className="result-grid-wrapper">
      <div className="result-grid-header">
        {hitCountLabel && <span className="label">{hitCountLabel}</span>}
        {rows.length > 0 && (
          <select
            className="sort-dropdown"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            {hasRelevance && <option value="relevance">Relevance</option>}
            <option value="date_desc">Date, newest first</option>
            <option value="date_asc">Date, oldest first</option>
          </select>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="grid-empty">No results match your search.</p>
      ) : (
        <div className="result-grid">
          {sortedRows.map((row) => (
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
              <button
                type="button"
                className="find-similar-button"
                onClick={(e) => { e.stopPropagation(); onFindSimilar(row) }}
              >
                Find similar
              </button>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
