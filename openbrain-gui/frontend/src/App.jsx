import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { formatDateTime } from './format.js'
import ThemeToggle from './ThemeToggle.jsx'
import PromptBar from './PromptBar.jsx'
import KeywordPanel from './KeywordPanel.jsx'
import ResultGrid from './ResultGrid.jsx'
import DeleteLogView from './DeleteLogView.jsx'
import ChangePopup from './ChangePopup.jsx'
import SummaryPopup from './SummaryPopup.jsx'
import KeywordGraph from './KeywordGraph.jsx'
import FilterBar from './FilterBar.jsx'
import CostView from './CostView'

const SEARCH_PAGE_SIZE = 25

export default function App() {
  const [stats, setStats] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [savedPrompts, setSavedPrompts] = useState([])
  const [selectedPromptId, setSelectedPromptId] = useState('')
  const [rows, setRows] = useState([])
  const [searchK, setSearchK] = useState(SEARCH_PAGE_SIZE)
  const [filters, setFilters] = useState({
    source: '', date_from: '', date_to: '', keywords: [], keyword_mode: 'or',
  })
  const [searching, setSearching] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [view, setView] = useState('results') // 'results' | 'deleteLog' | 'graph'
  const [editingRow, setEditingRow] = useState(null)
  const [viewingRow, setViewingRow] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [hasSearched, setHasSearched] = useState(false)
  // Bumped only when a genuinely new search/browse/find-similar result set
  // commits -- NOT when rows is updated in place by handleDelete/handleChangeSave.
  // ResultGrid keys its default-sort-reset off this instead of `rows` itself,
  // since `rows` also gets a new array reference from those in-place edits.
  const [resultsVersion, setResultsVersion] = useState(0)
  const promptTextareaRef = useRef(null)
  // Bumped at the start of every search/browse/find-similar; a request only
  // commits its results if it's still the most recent one when it resolves --
  // guards against a slower, stale request (e.g. filters changed mid-flight)
  // silently overwriting a newer one's results.
  const searchTokenRef = useRef(0)

  useEffect(() => {
    // Resolve both mount-time fetches before touching `error` once -- doing
    // it per-call let a later success silently clear a genuine earlier
    // failure (or vice versa) whenever the two requests failed/succeeded in
    // different orders.
    Promise.allSettled([api.getStats(), api.getPrompts()]).then(([statsResult, promptsResult]) => {
      setStats(statsResult.status === 'fulfilled' ? statsResult.value : null)
      setSavedPrompts(promptsResult.status === 'fulfilled' ? promptsResult.value : [])
      const failure = [statsResult, promptsResult].find((r) => r.status === 'rejected')
      setError(failure ? failure.reason.message : null)
    })
  }, [])

  const showNotice = (message) => {
    setNotice(message)
    setTimeout(() => setNotice(null), 3000)
  }

  const handleKeywordClick = (keyword) => {
    const el = promptTextareaRef.current
    const start = el?.selectionStart ?? prompt.length
    const end = el?.selectionEnd ?? prompt.length
    setPrompt(prompt.slice(0, start) + keyword + prompt.slice(end))
  }

  const activeFilterPayload = () => ({
    source: filters.source || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
    keywords: filters.keywords.length ? filters.keywords : undefined,
    keyword_mode: filters.keyword_mode,
  })

  const hasActiveFilters = Boolean(
    filters.source || filters.date_from || filters.date_to || filters.keywords.length
  )

  const runSearch = async (k) => {
    const token = ++searchTokenRef.current
    setSearching(true)
    try {
      const results = await api.search({ query: prompt, k, ...activeFilterPayload() })
      if (token !== searchTokenRef.current) return  // a newer request already won
      setRows(results)
      setSearchK(k)
      setResultsVersion((v) => v + 1)
      setError(null)
    } catch (err) {
      if (token === searchTokenRef.current) setError(err.message)
    } finally {
      if (token === searchTokenRef.current) setSearching(false)
    }
  }

  const runBrowse = async (n) => {
    const token = ++searchTokenRef.current
    setSearching(true)
    try {
      const results = await api.getRecent({ n, ...activeFilterPayload() })
      if (token !== searchTokenRef.current) return
      setRows(results)
      setSearchK(n)
      setResultsVersion((v) => v + 1)
      setError(null)
    } catch (err) {
      if (token === searchTokenRef.current) setError(err.message)
    } finally {
      if (token === searchTokenRef.current) setSearching(false)
    }
  }

  const handleSearch = async () => {
    setSelectedId(null)
    setView('results')
    setHasSearched(true)
    if (prompt.trim()) {
      await runSearch(SEARCH_PAGE_SIZE)
    } else {
      await runBrowse(SEARCH_PAGE_SIZE)
    }
  }

  const handleLoadMore = () =>
    prompt.trim() ? runSearch(searchK + SEARCH_PAGE_SIZE) : runBrowse(searchK + SEARCH_PAGE_SIZE)

  const handleFindSimilar = async (row) => {
    setSelectedId(null)
    setView('results')
    setHasSearched(true)
    const token = ++searchTokenRef.current
    setSearching(true)
    try {
      const results = await api.search({
        capture_id: row.id, k: SEARCH_PAGE_SIZE, ...activeFilterPayload(),
      })
      if (token !== searchTokenRef.current) return
      setRows(results)
      setSearchK(SEARCH_PAGE_SIZE)
      setResultsVersion((v) => v + 1)
      setError(null)
    } catch (err) {
      if (token === searchTokenRef.current) setError(err.message)
    } finally {
      if (token === searchTokenRef.current) setSearching(false)
    }
  }

  const handleSavePrompt = async () => {
    if (!prompt.trim()) return
    try {
      const created = await api.savePrompt(prompt)
      setSavedPrompts((prev) => [created, ...prev])
      setSelectedPromptId(created.id)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  const handleSelectSavedPrompt = (id) => {
    setSelectedPromptId(id)
    const found = savedPrompts.find((p) => String(p.id) === String(id))
    if (found) setPrompt(found.text)
  }

  const handleDeleteSavedPrompt = async () => {
    if (!selectedPromptId) return
    try {
      await api.deletePrompt(selectedPromptId)
      setSavedPrompts((prev) => prev.filter((p) => String(p.id) !== String(selectedPromptId)))
      setSelectedPromptId('')
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  const handleDelete = async () => {
    const row = rows.find((r) => r.id === selectedId)
    if (!row || !window.confirm('Delete this capture?')) return
    try {
      await api.deleteCapture(row.id, {
        subject_line: row.subject_line,
        keywords: row.keywords,
        source_url: row.source_url,
        created_at: row.created_at,
      })
      setRows((prev) => prev.filter((r) => r.id !== row.id))
      setSelectedId(null)
      setError(null)
      showNotice('Capture deleted.')
    } catch (err) {
      setError(err.message)
    }
  }

  const handleChangeSave = async (changes) => {
    try {
      const result = await api.updateCapture(editingRow.id, changes)
      setRows((prev) =>
        prev.map((r) =>
          r.id === editingRow.id
            ? { ...r, ...changes, ...(result.subject_line ? { subject_line: result.subject_line } : {}) }
            : r,
        ),
      )
      setEditingRow(null)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  const selectedRow = rows.find((r) => r.id === selectedId)
  const canActOnSelection = view === 'results' && !!selectedRow
  const canLoadMore = view === 'results' && rows.length > 0 && rows.length >= searchK
  const hitCountLabel = view === 'results' && hasSearched
    ? `${rows.length} result${rows.length === 1 ? '' : 's'}`
      + (hasActiveFilters && stats ? ` (filtered from ${stats.total})` : '')
    : null

  return (
    <div className="app">
      <header className="app-header">
        <h1>OpenBrain</h1>
        <ThemeToggle />
      </header>

      {error && <p className="error-banner">{error}</p>}
      {notice && <p className="notice-banner">{notice}</p>}

      {stats && (
        <p className="stats-line">
          {stats.total} captures ·{' '}
          {Object.entries(stats.by_source).map(([src, n]) => `${src}: ${n}`).join(', ')}
          {stats.first_capture && ` · first: ${formatDateTime(stats.first_capture)}`}
          {stats.last_capture && ` · last: ${formatDateTime(stats.last_capture)}`}
        </p>
      )}

      <div className="top-row">
        <div className="prompt-column">
          <PromptBar
            prompt={prompt}
            onPromptChange={setPrompt}
            promptTextareaRef={promptTextareaRef}
            savedPrompts={savedPrompts}
            selectedPromptId={selectedPromptId}
            onSelectSavedPrompt={handleSelectSavedPrompt}
            onSearch={handleSearch}
          />
          <FilterBar
            sources={stats ? Object.keys(stats.by_source) : []}
            filters={filters}
            onFiltersChange={setFilters}
          />
          <div className="prompt-actions">
            <button className="search-button" onClick={handleSearch} disabled={searching}>
              {searching ? 'Searching…' : 'Search'}
            </button>
            <button onClick={handleSavePrompt}>Save prompt</button>
            <button onClick={handleDeleteSavedPrompt} disabled={!selectedPromptId}>
              Delete prompt
            </button>
          </div>
        </div>
        <KeywordPanel onKeywordClick={handleKeywordClick} />
      </div>

      <div className="grid-actions">
        <button disabled={!canActOnSelection} onClick={() => setViewingRow(selectedRow)}>
          Summary
        </button>
        <button disabled={!canActOnSelection} onClick={() => setEditingRow(selectedRow)}>
          Change
        </button>
        <button disabled={!canActOnSelection} onClick={handleDelete}>
          Delete
        </button>
        <button onClick={() => setView((v) => (v === 'deleteLog' ? 'results' : 'deleteLog'))}>
          {view === 'deleteLog' ? 'Back to results' : 'Show delete log'}
        </button>
        <button onClick={() => setView((v) => (v === 'graph' ? 'results' : 'graph'))}>
          {view === 'graph' ? 'Back to results' : 'Show keyword graph'}
        </button>
        <button onClick={() => setView((v) => (v === 'cost' ? 'results' : 'cost'))}>
          {view === 'cost' ? 'Back to results' : 'Cost'}
        </button>
      </div>

      {view === 'deleteLog' ? (
        <DeleteLogView />
      ) : view === 'graph' ? (
        <KeywordGraph onKeywordClick={handleKeywordClick} />
      ) : view === 'cost' ? (
        <CostView />
      ) : searching ? (
        <p className="grid-empty">Searching…</p>
      ) : (
        <ResultGrid
          rows={rows}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onFindSimilar={handleFindSimilar}
          hitCountLabel={hitCountLabel}
          hasSearched={hasSearched}
          resultsVersion={resultsVersion}
        />
      )}

      {canLoadMore && (
        <button className="load-more" onClick={handleLoadMore} disabled={searching}>
          Load more
        </button>
      )}

      {editingRow && (
        <ChangePopup row={editingRow} onSave={handleChangeSave} onClose={() => setEditingRow(null)} />
      )}
      {viewingRow && (
        <SummaryPopup row={viewingRow} onClose={() => setViewingRow(null)} />
      )}
    </div>
  )
}
