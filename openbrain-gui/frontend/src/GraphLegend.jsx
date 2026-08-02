import { useRef, useState, useEffect } from 'react'
import { clusterColor } from './graphSimulation.js'

export default function GraphLegend({ clusters, activeClusters, onToggle, onToggleAll }) {
  const [hoveredClusterId, setHoveredClusterId] = useState(null)
  const [shown, setShown] = useState(null)
  const hoveredCluster = clusters.find((c) => c.cluster_id === hoveredClusterId)

  const allChecked = clusters.length > 0 && activeClusters.size === clusters.length
  const someChecked = activeClusters.size > 0 && !allChecked
  const selectAllRef = useRef(null)
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someChecked
  }, [someChecked])

  const handleShow = () => {
    // Every capture belongs to exactly one cluster, so no dedup needed --
    // just flatten the already-loaded per-cluster membership, no new fetch.
    setShown(
      clusters
        .filter((c) => activeClusters.has(c.cluster_id))
        .flatMap((c) => c.captures.map((cap) => ({ ...cap, cluster_label: c.label }))),
    )
  }

  return (
    <div className="graph-legend">
      <p className="label">Clusters</p>
      <label className="graph-legend-row graph-legend-select-all">
        <input
          ref={selectAllRef}
          type="checkbox"
          checked={allChecked}
          onChange={() => onToggleAll(!allChecked)}
        />
        Select all
      </label>
      {clusters.map((cluster) => (
        <label
          key={cluster.cluster_id}
          className="graph-legend-row"
          onMouseEnter={() => setHoveredClusterId(cluster.cluster_id)}
          onMouseLeave={() => setHoveredClusterId(null)}
        >
          <input
            type="checkbox"
            checked={activeClusters.has(cluster.cluster_id)}
            onChange={() => onToggle(cluster.cluster_id)}
          />
          <span style={{ color: clusterColor(cluster.cluster_id) }}>●</span>
          {cluster.label} ({cluster.size} captures)
        </label>
      ))}
      <button
        type="button"
        className="graph-legend-show"
        disabled={activeClusters.size === 0}
        onClick={handleShow}
      >
        Show
      </button>
      {hoveredCluster && (
        <div className="graph-tooltip">
          <strong>{hoveredCluster.label} — {hoveredCluster.size} captures</strong>
          <ul>
            {hoveredCluster.captures.slice(0, 20).map((c) => (
              <li key={c.id}>{c.central ? '★ ' : ''}{c.subject_line}</li>
            ))}
            {hoveredCluster.captures.length > 20 && (
              <li>+{hoveredCluster.captures.length - 20} more</li>
            )}
          </ul>
        </div>
      )}
      {shown && (
        <div className="graph-legend-shown">
          <div className="graph-legend-shown-header">
            <span className="label">{shown.length} capture{shown.length === 1 ? '' : 's'}</span>
            <button type="button" className="link-button" onClick={() => setShown(null)}>
              Close
            </button>
          </div>
          <ul>
            {shown.map((c) => (
              <li key={c.id}>
                {c.central ? '★ ' : ''}{c.subject_line}
                <span className="cost-note"> — {c.cluster_label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
