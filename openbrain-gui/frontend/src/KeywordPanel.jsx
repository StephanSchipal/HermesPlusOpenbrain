import { useEffect, useState } from 'react'
import { api } from './api.js'

export default function KeywordPanel({ onKeywordClick }) {
  const [filter, setFilter] = useState('')
  const [keywords, setKeywords] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    const timer = setTimeout(() => {
      api.getKeywords(filter)
        .then((result) => { setKeywords(result); setError(null) })
        .catch((err) => { setKeywords([]); setError(err.message) })
    }, 200)
    return () => clearTimeout(timer)
  }, [filter])

  return (
    <div className="keyword-panel">
      <input
        className="keyword-filter"
        placeholder="Filter keywords…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {error && <p className="error-banner">{error}</p>}
      <div className="keyword-list">
        {keywords.map((k) => (
          <button key={k.keyword} className="keyword-chip" onClick={() => onKeywordClick(k.keyword)}>
            {k.keyword} ({k.count})
          </button>
        ))}
      </div>
    </div>
  )
}
