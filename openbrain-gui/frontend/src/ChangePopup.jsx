import { useState } from 'react'

export default function ChangePopup({ row, onSave, onClose }) {
  const [mode, setMode] = useState('short') // 'short' | 'full'
  const [summary, setSummary] = useState(row.summary)
  const [rawText, setRawText] = useState(row.raw_text || '')
  const [keywords, setKeywords] = useState(row.keywords.join(', '))

  const trimmedSummary = summary.trim()
  const trimmedRawText = rawText.trim()
  const keywordList = keywords.split(',').map((k) => k.trim()).filter(Boolean)
  const canSave = trimmedSummary.length > 0 && keywordList.length > 0

  const handleSave = () => {
    if (!canSave) return
    const changes = { keywords: keywordList }
    if (trimmedSummary !== row.summary) changes.summary = trimmedSummary
    if (trimmedRawText !== (row.raw_text || '')) changes.raw_text = trimmedRawText
    onSave(changes)
  }

  return (
    <div className="popup-overlay">
      <div className="popup">
        <div className="popup-header-row">
          <h3>Change entry</h3>
          <div className="mode-toggle">
            <button
              type="button"
              className={mode === 'short' ? 'active' : ''}
              onClick={() => setMode('short')}
            >
              Short
            </button>
            <button
              type="button"
              className={mode === 'full' ? 'active' : ''}
              onClick={() => setMode('full')}
            >
              Full
            </button>
          </div>
        </div>
        {mode === 'short' ? (
          <label>
            Summary
            <textarea rows={12} value={summary} onChange={(e) => setSummary(e.target.value)} />
          </label>
        ) : (
          <label>
            Raw text
            <textarea rows={12} value={rawText} onChange={(e) => setRawText(e.target.value)} />
          </label>
        )}
        <label>
          Keywords (comma-separated)
          <input value={keywords} onChange={(e) => setKeywords(e.target.value)} />
        </label>
        <div className="popup-actions">
          <button onClick={handleSave} disabled={!canSave}>Save</button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
