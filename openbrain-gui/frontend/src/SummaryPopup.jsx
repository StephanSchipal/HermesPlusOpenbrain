import { useState } from 'react'

export default function SummaryPopup({ row, onClose }) {
  const [mode, setMode] = useState('short') // 'short' | 'full'
  const text = mode === 'short' ? row.summary : (row.raw_text || '')

  return (
    <div className="popup-overlay">
      <div className="popup">
        <div className="popup-header-row">
          <h3>Summary</h3>
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
        <p className="popup-summary-text">{text}</p>
        <div className="popup-actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
