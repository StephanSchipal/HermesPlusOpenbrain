export default function SummaryPopup({ row, onClose }) {
  return (
    <div className="popup-overlay">
      <div className="popup">
        <h3>Summary</h3>
        <p className="popup-summary-text">{row.summary}</p>
        <div className="popup-actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
