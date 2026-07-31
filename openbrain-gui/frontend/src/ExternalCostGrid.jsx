import { useEffect, useState } from 'react'
import { api } from './api'

const PERIODS = ['yearly', 'monthly', 'onetime', 'none']

function blankRow(sortOrder) {
  return {
    id: null, name: '', period: 'monthly', amount: null, entered_currency: 'USD',
    url: '', comments: '', compare_to_estimate: false, sort_order: sortOrder,
    usd: null, eur: null,
  }
}

// Two decimals for money, blank for "not known yet" (no rate fetched).
function money(value) {
  return value == null ? '' : Number(value).toFixed(2)
}

export default function ExternalCostGrid({ onTotalsChange }) {
  const [rows, setRows] = useState([])
  const [rate, setRate] = useState(null)
  const [rateInput, setRateInput] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    const data = await api.getExternalCosts()
    setRows(data.rows)
    setRate(data.rate)
    setRateInput(data.rate ? String(data.rate.usd_to_eur) : '')
    setDirty(false)
    onTotalsChange?.(data.totals)
  }

  useEffect(() => { load().catch((e) => setError(e.message)) }, [])

  function patch(idx, changes) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...changes } : r)))
    setDirty(true)
  }

  // Typing in one currency box makes THAT currency authoritative; the other
  // is derived on the next load. See design spec section 6.1.
  function setAmount(idx, currency, raw) {
    const amount = raw === '' ? null : Number(raw)
    if (raw !== '' && Number.isNaN(amount)) return
    const factor = rate?.usd_to_eur
    patch(idx, {
      amount,
      entered_currency: currency,
      usd: currency === 'USD' ? amount : (factor && amount != null ? amount / factor : null),
      eur: currency === 'EUR' ? amount : (factor && amount != null ? amount * factor : null),
    })
  }

  async function handleSave() {
    setBusy(true); setError(null)
    try {
      await api.saveExternalCosts(rows.map(({ usd, eur, ...row }) => row))
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function handleDelete() {
    if (selectedIdx == null) return
    const row = rows[selectedIdx]
    if (!window.confirm(`Delete row "${row.name || '(unnamed)'}"?`)) return
    setBusy(true); setError(null)
    try {
      if (row.id != null) await api.deleteExternalCost(row.id)
      setSelectedIdx(null)
      if (row.id == null) {
        setRows((prev) => prev.filter((_, i) => i !== selectedIdx))
      } else {
        await load()
      }
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function handleRefreshRate() {
    setBusy(true); setError(null)
    try {
      const { rate: fresh } = await api.refreshFx()
      setRate(fresh); setRateInput(String(fresh.usd_to_eur))
      await load()
    } catch (e) {
      setError(`${e.message} — showing the previous rate`)
    } finally { setBusy(false) }
  }

  async function handleManualRate() {
    // Fires on blur, so tabbing through an untouched box must stay silent --
    // only complain about something the user actually typed. Before the first
    // rate is ever fetched the box is legitimately empty.
    if (rateInput.trim() === '') return
    const value = Number(rateInput)
    if (!value || value <= 0) { setError('Rate must be a positive number'); return }
    if (rate && value === rate.usd_to_eur) return
    setBusy(true); setError(null)
    try {
      const { rate: fresh } = await api.setFx(value)
      setRate(fresh)
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <section className="external-costs">
      <div className="external-costs-header">
        <h3>External costs</h3>
        <div className="fx-control">
          <label>
            Rate $ → €
            <input
              value={rateInput}
              onChange={(e) => setRateInput(e.target.value)}
              onBlur={handleManualRate}
              size={8}
            />
          </label>
          <button onClick={handleRefreshRate} disabled={busy} title="Fetch ECB daily rate">⟳</button>
          <span className="fx-meta">
            {rate ? `${rate.source}, ${rate.fetched_at.slice(0, 10)}` : 'no rate yet'}
          </span>
        </div>
      </div>

      {error && <p className="external-costs-error">{error}</p>}

      <table className="external-costs-table">
        <thead>
          <tr>
            <th /><th>Name</th><th>Period</th><th>$</th><th>€</th>
            <th>URL</th><th>Comments</th><th title="Compare to Hermes estimate">≈</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={row.id ?? `new-${idx}`} className={selectedIdx === idx ? 'selected' : ''}>
              <td>
                <input type="radio" name="external-cost-row" checked={selectedIdx === idx}
                       onChange={() => setSelectedIdx(idx)} />
              </td>
              <td><input value={row.name} onChange={(e) => patch(idx, { name: e.target.value })} /></td>
              <td>
                <select value={row.period} onChange={(e) => patch(idx, { period: e.target.value })}>
                  {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </td>
              <td>
                <input className="num" value={money(row.usd)}
                       onChange={(e) => setAmount(idx, 'USD', e.target.value)} />
              </td>
              <td>
                <input className="num" value={money(row.eur)}
                       onChange={(e) => setAmount(idx, 'EUR', e.target.value)} />
              </td>
              <td>
                <input value={row.url || ''} onChange={(e) => patch(idx, { url: e.target.value })} />
                {row.url && (
                  <a href={row.url} target="_blank" rel="noopener noreferrer" title="Open billing page">↗</a>
                )}
              </td>
              <td>
                <input value={row.comments || ''}
                       onChange={(e) => patch(idx, { comments: e.target.value })} />
              </td>
              <td>
                <input type="checkbox" checked={!!row.compare_to_estimate}
                       onChange={(e) => patch(idx, { compare_to_estimate: e.target.checked })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="external-costs-actions">
        <button onClick={() => { setRows((p) => [...p, blankRow(p.length)]); setDirty(true) }}>
          Add row
        </button>
        <button onClick={handleDelete} disabled={selectedIdx == null || busy}>Delete row</button>
        <button onClick={handleSave} disabled={!dirty || busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </section>
  )
}
