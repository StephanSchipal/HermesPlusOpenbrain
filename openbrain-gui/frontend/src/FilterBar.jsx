import { useEffect, useState } from 'react'
import { api } from './api.js'

function isoDate(d) {
  // Local calendar date, not UTC -- new Date(y, 0, 1) below builds local
  // midnight, and toISOString()'s UTC conversion would shift that back a
  // day in any timezone ahead of UTC.
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

const QUICK_RANGES = [
  { value: 'all', label: 'All' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last month' },
  { value: 'year', label: 'This year' },
]

export default function FilterBar({ sources, filters, onFiltersChange }) {
  const [keywordInput, setKeywordInput] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [quickRange, setQuickRange] = useState('all')

  useEffect(() => {
    if (!keywordInput) { setSuggestions([]); return }
    let ignore = false
    const timer = setTimeout(() => {
      api.getKeywords(keywordInput)
        .then((result) => { if (!ignore) setSuggestions(result) })
        .catch(() => { if (!ignore) setSuggestions([]) })
    }, 200)
    return () => { ignore = true; clearTimeout(timer) }
  }, [keywordInput])

  const update = (patch) => onFiltersChange({ ...filters, ...patch })

  const applyQuickRange = (days) => {
    const to = new Date()
    const from = days === null
      ? new Date(to.getFullYear(), 0, 1)
      : new Date(to.getTime() - days * 24 * 60 * 60 * 1000)
    update({ date_from: isoDate(from), date_to: isoDate(to) })
  }

  const handleQuickRangeChange = (value) => {
    setQuickRange(value)
    if (value === 'all') update({ date_from: '', date_to: '' })
    else if (value === 'year') applyQuickRange(null)
    else applyQuickRange(Number(value))
  }

  const addKeyword = (kw) => {
    if (!kw || filters.keywords.includes(kw)) return
    update({ keywords: [...filters.keywords, kw] })
    setKeywordInput('')
    setSuggestions([])
  }

  const removeKeyword = (kw) => update({ keywords: filters.keywords.filter((k) => k !== kw) })

  const reset = () => {
    setQuickRange('all')
    onFiltersChange({ source: '', date_from: '', date_to: '', keywords: [], keyword_mode: 'or' })
  }

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
        <select
          className="filter-quick-range"
          value={quickRange}
          onChange={(e) => handleQuickRangeChange(e.target.value)}
        >
          {QUICK_RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
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
            if (e.key === 'Enter') { e.preventDefault(); addKeyword(keywordInput.trim()) }
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
