import { useState } from 'react'

export default function ChangePopup({ row, onSave, onClose }) {
  const [summary, setSummary] = useState(row.summary)
  const [keywords, setKeywords] = useState(row.keywords.join(', '))

  const trimmedSummary = summary.trim()
  const keywordList = keywords.split(',').map((k) => k.trim()).filter(Boolean)
  const canSave = trimmedSummary.length > 0 && keywordList.length > 0

  const handleSave = () => {
    if (!canSave) return
    onSave({ summary: trimmedSummary, keywords: keywordList })
  }

  return (
    <div className="popup-overlay">
      <div className="popup">
        <h3>Change entry</h3>
        <label>
          Summary
          <textarea rows={4} value={summary} onChange={(e) => setSummary(e.target.value)} />
        </label>
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
