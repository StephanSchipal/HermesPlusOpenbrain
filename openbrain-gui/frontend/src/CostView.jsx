import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import CostSummary from './CostSummary'
import CostChart from './CostChart'
import CostExplainPopup from './CostExplainPopup'
import CostTables from './CostTables'
import CostEfficiency from './CostEfficiency'
import SessionDetail from './SessionDetail'
import CostConfig from './CostConfig'
import ExternalCostGrid from './ExternalCostGrid'
import CostReportPicker from './CostReportPicker'
import { dateRangeLabel, reportName, formatDateTime } from './format'

const RANGES = [
  { days: 1, label: 'Today', today: true },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
]

export default function CostView() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState({})
  const [unavailable, setUnavailable] = useState(null)
  const [selectedSession, setSelectedSession] = useState(null)
  const [chartGroup, setChartGroup] = useState('model')
  const [series, setSeries] = useState(null)
  const [explaining, setExplaining] = useState(false)
  // A loaded saved report replaces the live Part 1 panels in place -- see
  // `display` below -- rather than opening a separate view, so the two-
  // windows-side-by-side comparison workflow just works: each window
  // independently shows live or a chosen saved report.
  const [viewingReport, setViewingReport] = useState(null)
  const [showPicker, setShowPicker] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState(null)

  const load = useCallback(async () => {
    setUnavailable(null)
    try {
      // One dashboard call covers every panel: the backend runs them all off a
      // single snapshot of Hermes' 42 MB database rather than one copy each.
      const [dashboard, summary, config] = await Promise.all([
        api.getCostDashboard(days), api.getCostSummary(days), api.getCostConfig(),
      ])
      setData({ ...dashboard, summary, config })
    } catch (e) {
      // 503 = /hermes-data is not mounted. Part 2 below still renders.
      setUnavailable(e.message)
      setData({})
      setSelectedSession(null)
    }
  }, [days])

  useEffect(() => { load() }, [load])

  // The ledger lives in gui.db, so the chart loads independently of the Hermes
  // mount -- it keeps working even when every other panel is 503.
  useEffect(() => {
    api.getCostTimeseries(days, chartGroup).then(setSeries).catch(() => setSeries(null))
  }, [days, chartGroup])

  const activeRange = RANGES.find((r) => r.days === days)
  // Everything below reads `display`/`displayUnavailable`, not `data`/
  // `unavailable`, directly -- a saved report is a frozen payload with the
  // exact same shape `data` already has, so every existing panel renders it
  // unchanged. The chart stays live either way -- it isn't part of what a
  // saved report captures.
  const display = viewingReport ? viewingReport.payload : data
  const displayUnavailable = viewingReport ? null : unavailable

  const selectRange = (d) => {
    setDays(d)
    setViewingReport(null)
  }

  const handleSaveReport = async () => {
    const name = reportName(days, activeRange?.today)
    const label = dateRangeLabel(days, activeRange?.today)
    setSaving(true)
    setSaveStatus(null)
    try {
      await api.saveCostReport(name, { days, range_label: label, payload: data })
      setSaveStatus({ ok: true, message: `Saved as ${name}` })
    } catch (e) {
      setSaveStatus({ ok: false, message: `Save failed: ${e.message}` })
    } finally {
      setSaving(false)
      setTimeout(() => setSaveStatus(null), 4000)
    }
  }

  const handleLoadReport = async (name) => {
    const report = await api.getCostReport(name)
    setViewingReport(report)
    setShowPicker(false)
  }

  const canSave = Boolean(data.summary) && !viewingReport

  return (
    <div className="cost-view">
      <section className="hermes-usage panel-surface cost-divider">
        <div className="cost-range">
          {RANGES.map(({ days: d, label }) => (
            <button key={d} className={days === d ? 'active' : ''} onClick={() => selectRange(d)}>
              {label}
            </button>
          ))}
          <span className="cost-range-label">
            {dateRangeLabel(days, activeRange?.today)}
          </span>
          <div className="cost-report-actions">
            <button type="button" disabled={!canSave || saving} onClick={handleSaveReport}>
              {saving ? 'Saving…' : 'Save report'}
            </button>
            <button type="button" onClick={() => setShowPicker(true)}>
              Load stored report
            </button>
            {saveStatus && (
              <span className={saveStatus.ok ? 'cost-report-status' : 'cost-report-status cost-warning'}>
                {saveStatus.message}
              </span>
            )}
          </div>
        </div>

        {viewingReport && (
          <p className="cost-report-banner">
            Viewing saved report: <strong>{viewingReport.name}</strong>
            {' '}(saved {formatDateTime(viewingReport.saved_at)}) —{' '}
            <button type="button" className="link-button" onClick={() => setViewingReport(null)}>
              back to live
            </button>
          </p>
        )}

        <CostSummary summary={display.summary} unavailable={displayUnavailable}
                     onExplain={() => setExplaining(true)} />

        {explaining && display.summary && (
          <CostExplainPopup summary={display.summary} onClose={() => setExplaining(false)} />
        )}

        <CostChart series={series} group={chartGroup} onGroupChange={setChartGroup} />

        {!displayUnavailable && display.summary && (
          <>
            <CostTables
              byModel={display.by_model}
              byPlatform={display.by_platform}
              bySession={display.by_session}
              onSelectSession={setSelectedSession}
            />
            {selectedSession && (
              <SessionDetail sessionId={selectedSession} onClose={() => setSelectedSession(null)} />
            )}
            <CostEfficiency hermes={display.summary.hermes} efficiency={display.efficiency} />
            <CostConfig tools={display.top_tools} promptBudget={display.prompt_budget} config={display.config} />
          </>
        )}
      </section>

      {showPicker && (
        <CostReportPicker onSelect={handleLoadReport} onClose={() => setShowPicker(false)} />
      )}

      <ExternalCostGrid />
    </div>
  )
}
