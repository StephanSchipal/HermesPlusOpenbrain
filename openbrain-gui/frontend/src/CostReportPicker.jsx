import { useEffect, useState } from 'react'
import { api } from './api'
import { formatDateTime } from './format'

export default function CostReportPicker({ onSelect, onClose }) {
  const [reports, setReports] = useState(null)
  const [error, setError] = useState(null)
  const [loadingName, setLoadingName] = useState(null)

  useEffect(() => {
    api.listCostReports().then(setReports).catch((e) => setError(e.message))
  }, [])

  const handleSelect = async (name) => {
    setLoadingName(name)
    setError(null)
    try {
      await onSelect(name)
    } catch (e) {
      setError(e.message)
      setLoadingName(null)
    }
  }

  return (
    <div className="popup-overlay">
      <div className="popup">
        <div className="popup-header-row">
          <h3>Stored cost reports</h3>
        </div>

        {error && <p className="external-costs-error">{error}</p>}

        {!reports ? (
          <p className="cost-loading">Loading…</p>
        ) : reports.length === 0 ? (
          <p className="cost-note">No saved reports yet -- use "Save report" on the Cost page first.</p>
        ) : (
          <ul className="cost-report-list">
            {reports.map((r) => (
              <li key={r.name}>
                <button type="button" disabled={loadingName != null} onClick={() => handleSelect(r.name)}>
                  <strong>{r.name}</strong>
                  <span className="cost-note">
                    {' '}— {r.range_label} · saved {formatDateTime(r.saved_at)}
                    {loadingName === r.name ? ' · loading…' : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="popup-actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
