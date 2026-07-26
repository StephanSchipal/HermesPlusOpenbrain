import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { renderGraph } from './graphSimulation.js'
import GraphLegend from './GraphLegend.jsx'

const WIDTH = 700
const HEIGHT = 420

export default function KeywordGraph({ onKeywordClick }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')
  const [activeClusters, setActiveClusters] = useState(null)
  const [hoveredKeyword, setHoveredKeyword] = useState(null)
  const svgRef = useRef(null)
  const graphApiRef = useRef(null)
  // renderGraph() binds onNodeClick once, inside the [data]-keyed effect below,
  // which does NOT re-run on every App.jsx render -- so a plain `onKeywordClick`
  // reference would go stale the moment the parent's prompt state (and thus its
  // non-memoized handler) changes, silently dropping subsequent bubble clicks.
  // Route through a ref that's kept current every render instead.
  const onKeywordClickRef = useRef(onKeywordClick)
  useEffect(() => {
    onKeywordClickRef.current = onKeywordClick
  })

  const load = () => {
    setError(null)
    setHoveredKeyword(null)
    api.getGraph()
      .then((result) => {
        setData(result)
        setActiveClusters(
          result.clusters ? new Set(result.clusters.map((c) => c.cluster_id)) : null,
        )
      })
      .catch((err) => {
        setError(err.message)
        graphApiRef.current?.destroy()
        graphApiRef.current = null
      })
  }

  useEffect(load, [])

  useEffect(() => {
    if (!data || data.error || !svgRef.current) return
    graphApiRef.current = renderGraph(svgRef.current, {
      keywords: data.keywords,
      clusterCount: data.clusters.length,
      width: WIDTH,
      height: HEIGHT,
      onNodeClick: (keyword) => onKeywordClickRef.current(keyword),
      onNodeHover: setHoveredKeyword,
    })
    return () => graphApiRef.current?.destroy()
  }, [data])

  useEffect(() => {
    if (!graphApiRef.current || !activeClusters) return
    const needle = filter.trim().toLowerCase()
    graphApiRef.current.setVisible(
      (d) => activeClusters.has(d.cluster_id) && (!needle || d.keyword.toLowerCase().includes(needle)),
    )
  }, [filter, activeClusters])

  if (error) return <p className="error-banner">{error}</p>
  if (!data) return <p className="grid-empty">Loading graph…</p>
  if (data.error === 'not_enough_captures') {
    return (
      <p className="grid-empty">
        Not enough captures yet to build a graph — need at least 4, you have {data.count}.
      </p>
    )
  }

  const toggleCluster = (clusterId) => {
    setActiveClusters((prev) => {
      const next = new Set(prev)
      if (next.has(clusterId)) next.delete(clusterId)
      else next.add(clusterId)
      return next
    })
  }

  return (
    <div className="keyword-graph">
      <div className="keyword-graph-toolbar">
        <input
          className="keyword-filter"
          placeholder="Filter keywords…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button onClick={load}>Refresh</button>
      </div>
      <div className="keyword-graph-body">
        <div className="keyword-graph-canvas">
          <svg ref={svgRef} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" height={HEIGHT} />
          <div className="graph-zoom-controls">
            <button onClick={() => graphApiRef.current?.zoomIn()}>+</button>
            <button onClick={() => graphApiRef.current?.zoomOut()}>−</button>
          </div>
          {hoveredKeyword && (
            <div className="graph-tooltip">
              <strong>{hoveredKeyword.keyword} ({hoveredKeyword.count})</strong>
              <ul>
                {hoveredKeyword.captures.slice(0, 3).map((c) => (
                  <li key={c.id}>{c.central ? '★ ' : ''}{c.subject_line}</li>
                ))}
                {hoveredKeyword.captures.length > 3 && (
                  <li>+{hoveredKeyword.captures.length - 3} more</li>
                )}
              </ul>
            </div>
          )}
        </div>
        <GraphLegend clusters={data.clusters} activeClusters={activeClusters} onToggle={toggleCluster} />
      </div>
    </div>
  )
}
