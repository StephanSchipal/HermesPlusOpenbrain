import { useEffect, useState } from 'react'
import { api } from './api.js'

export default function KeywordPanel({ onKeywordClick }) {
  const [filter, setFilter] = useState('')
  const [keywords, setKeywords] = useState([])

  useEffect(() => {
    const timer = setTimeout(() => {
      api.getKeywords(filter).then(setKeywords).catch(() => setKeywords([]))
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
