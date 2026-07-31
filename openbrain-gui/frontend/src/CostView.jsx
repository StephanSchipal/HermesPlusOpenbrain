import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import CostSummary from './CostSummary'
import CostChart from './CostChart'
import CostTables from './CostTables'
import CostEfficiency from './CostEfficiency'
import SessionDetail from './SessionDetail'
import CostConfig from './CostConfig'
import ExternalCostGrid from './ExternalCostGrid'

const RANGES = [7, 30, 90]

export default function CostView() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState({})
  const [unavailable, setUnavailable] = useState(null)
  const [selectedSession, setSelectedSession] = useState(null)
  const [chartGroup, setChartGroup] = useState('model')
  const [series, setSeries] = useState(null)

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

  return (
    <div className="cost-view">
      <div className="cost-range">
        {RANGES.map((d) => (
          <button key={d} className={days === d ? 'active' : ''} onClick={() => setDays(d)}>
            {d} days
          </button>
        ))}
      </div>

      <CostSummary summary={data.summary} unavailable={unavailable} />

      <CostChart series={series} group={chartGroup} onGroupChange={setChartGroup} />

      {!unavailable && data.summary && (
        <>
          <CostTables
            byModel={data.by_model}
            byPlatform={data.by_platform}
            bySession={data.by_session}
            onSelectSession={setSelectedSession}
          />
          {selectedSession && (
            <SessionDetail sessionId={selectedSession} onClose={() => setSelectedSession(null)} />
          )}
          <CostEfficiency hermes={data.summary.hermes} efficiency={data.efficiency} />
          <CostConfig tools={data.top_tools} promptBudget={data.prompt_budget} config={data.config} />
        </>
      )}

      <ExternalCostGrid />
    </div>
  )
}
