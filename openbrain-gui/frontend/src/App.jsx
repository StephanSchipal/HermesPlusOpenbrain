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

const SEARCH_PAGE_SIZE = 25

export default function App() {
  const [stats, setStats] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [savedPrompts, setSavedPrompts] = useState([])
  const [selectedPromptId, setSelectedPromptId] = useState('')
  const [rows, setRows] = useState([])
  const [searchK, setSearchK] = useState(SEARCH_PAGE_SIZE)
  const [searching, setSearching] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [view, setView] = useState('results') // 'results' | 'deleteLog' | 'graph'
  const [editingRow, setEditingRow] = useState(null)
  const [viewingRow, setViewingRow] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const promptTextareaRef = useRef(null)

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

  const runSearch = async (k) => {
    setSearching(true)
    try {
      const results = await api.search(prompt, k)
      setRows(results)
      setSearchK(k)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const handleSearch = async () => {
    if (!prompt.trim()) return
    setSelectedId(null)
    setView('results')
    await runSearch(SEARCH_PAGE_SIZE)
  }

  const handleLoadMore = () => runSearch(searchK + SEARCH_PAGE_SIZE)

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
        <PromptBar
          prompt={prompt}
          onPromptChange={setPrompt}
          promptTextareaRef={promptTextareaRef}
          savedPrompts={savedPrompts}
          selectedPromptId={selectedPromptId}
          onSelectSavedPrompt={handleSelectSavedPrompt}
          onSearch={handleSearch}
          onSavePrompt={handleSavePrompt}
          onDeleteSavedPrompt={handleDeleteSavedPrompt}
          searching={searching}
        />
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
      </div>

      {view === 'deleteLog' ? (
        <DeleteLogView />
      ) : view === 'graph' ? (
        <KeywordGraph onKeywordClick={handleKeywordClick} />
      ) : searching ? (
        <p className="grid-empty">Searching…</p>
      ) : (
        <ResultGrid rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
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
