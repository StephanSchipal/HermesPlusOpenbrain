import { useEffect, useState } from 'react'
import { api } from './api.js'

function isoDate(d) {
  return d.toISOString().slice(0, 10)
}

export default function FilterBar({ sources, filters, onFiltersChange }) {
  const [keywordInput, setKeywordInput] = useState('')
  const [suggestions, setSuggestions] = useState([])

  useEffect(() => {
    if (!keywordInput) { setSuggestions([]); return }
    const timer = setTimeout(() => {
      api.getKeywords(keywordInput)
        .then(setSuggestions)
        .catch(() => setSuggestions([]))
    }, 200)
    return () => clearTimeout(timer)
  }, [keywordInput])

  const update = (patch) => onFiltersChange({ ...filters, ...patch })

  const applyQuickRange = (days) => {
    const to = new Date()
    const from = days === null
      ? new Date(to.getFullYear(), 0, 1)
      : new Date(to.getTime() - days * 24 * 60 * 60 * 1000)
    update({ date_from: isoDate(from), date_to: isoDate(to) })
  }

  const addKeyword = (kw) => {
    if (!kw || filters.keywords.includes(kw)) return
    update({ keywords: [...filters.keywords, kw] })
    setKeywordInput('')
    setSuggestions([])
  }

  const removeKeyword = (kw) => update({ keywords: filters.keywords.filter((k) => k !== kw) })

  const reset = () =>
    onFiltersChange({ source: '', date_from: '', date_to: '', keywords: [], keyword_mode: 'or' })

  return (
    <div className="filter-bar">
      <select
        className="filter-source"
        value={filters.source}
        onChange={(e) => update({ source: e.target.value })}
      >
        <option value="">All sources</option>
        {sources.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>

      <div className="filter-dates">
        <input
          type="date"
          className="filter-date"
          value={filters.date_from}
          onChange={(e) => update({ date_from: e.target.value })}
        />
        <span>–</span>
        <input
          type="date"
          className="filter-date"
          value={filters.date_to}
          onChange={(e) => update({ date_to: e.target.value })}
        />
        <button type="button" onClick={() => applyQuickRange(7)}>7 days</button>
        <button type="button" onClick={() => applyQuickRange(30)}>1 month</button>
        <button type="button" onClick={() => applyQuickRange(null)}>This year</button>
      </div>

      <div className="filter-keywords">
        {filters.keywords.map((kw) => (
          <button key={kw} type="button" className="keyword-chip" onClick={() => removeKeyword(kw)}>
            {kw} ✕
          </button>
        ))}
        <input
          className="keyword-filter"
          placeholder="Add keyword filter…"
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); addKeyword(keywordInput) }
          }}
        />
        {suggestions.length > 0 && (
          <div className="keyword-suggestions">
            {suggestions.map((s) => (
              <button key={s.keyword} type="button" onClick={() => addKeyword(s.keyword)}>
                {s.keyword} ({s.count})
              </button>
            ))}
          </div>
        )}
        <select
          className="keyword-mode-toggle"
          value={filters.keyword_mode}
          onChange={(e) => update({ keyword_mode: e.target.value })}
        >
          <option value="or">OR</option>
          <option value="and">AND</option>
        </select>
      </div>

      <button type="button" onClick={reset}>Reset filters</button>
    </div>
  )
}
