import { useEffect, useState } from 'react'
import { api } from './api.js'

export default function KeywordPanel({ onKeywordClick, onCountChange }) {
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

  // Reported up rather than rendered here -- the count sits next to the
  // capture stats line above this panel, not inside it (see App.jsx).
  useEffect(() => { onCountChange?.(keywords.length) }, [keywords, onCountChange])

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
