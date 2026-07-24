import { useState } from 'react'

export default function ChangePopup({ row, onSave, onClose }) {
  const [summary, setSummary] = useState(row.summary)
  const [keywords, setKeywords] = useState(row.keywords.join(', '))

  const handleSave = () => {
    const trimmedSummary = summary.trim()
    const keywordList = keywords.split(',').map((k) => k.trim()).filter(Boolean)
    if (!trimmedSummary || keywordList.length === 0) return
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
          <button onClick={handleSave}>Save</button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
