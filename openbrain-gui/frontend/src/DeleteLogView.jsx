import { useEffect, useState } from 'react'
import { api } from './api.js'

export default function DeleteLogView() {
  const [entries, setEntries] = useState([])

  useEffect(() => {
    api.getDeleteLog().then(setEntries).catch(() => setEntries([]))
  }, [])

  if (entries.length === 0) {
    return <p className="grid-empty">No deletions logged yet.</p>
  }
  return (
    <div className="result-grid">
      {entries.map((entry) => (
        <div key={entry.id} className="result-row result-row--readonly">
          <span className="result-id">{entry.capture_id.slice(0, 8)}</span>
          <div className="result-body">
            <div className="result-subject">{entry.subject_line}</div>
            <div className="result-meta">{entry.source_url}</div>
            <div className="result-meta">
              keywords: {entry.keywords.join(', ')} · deleted {entry.deleted_at}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
