import { useEffect, useState } from 'react'
import { api } from './api.js'
import { formatDateTime } from './format.js'

const PAGE_SIZE = 50

export default function DeleteLogView() {
  const [entries, setEntries] = useState([])
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getDeleteLog(limit)
      .then((result) => { setEntries(result); setError(null) })
      .catch((err) => { setEntries([]); setError(err.message) })
  }, [limit])

  if (error) {
    return <p className="error-banner">{error}</p>
  }
  if (entries.length === 0) {
    return <p className="grid-empty">No deletions logged yet.</p>
  }
  return (
    <>
      <div className="result-grid">
        {entries.map((entry) => (
          <div key={entry.id} className="result-row result-row--readonly">
            <span className="result-id">{entry.capture_id.slice(0, 8)}</span>
            <div className="result-body">
              <div className="result-subject">{entry.subject_line}</div>
              <div className="result-meta">
                {entry.source_url && (
                  <a href={entry.source_url} target="_blank" rel="noopener noreferrer">
                    {entry.source_url}
                  </a>
                )}
              </div>
              <div className="result-meta">
                keywords: {entry.keywords.join(', ')} · deleted {formatDateTime(entry.deleted_at)}
              </div>
            </div>
          </div>
        ))}
      </div>
      {entries.length >= limit && (
        <button className="load-more" onClick={() => setLimit((l) => l + PAGE_SIZE)}>
          Load more
        </button>
      )}
    </>
  )
}
