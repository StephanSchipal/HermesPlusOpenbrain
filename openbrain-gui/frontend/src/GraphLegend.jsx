import { useState } from 'react'
import { clusterColor } from './graphSimulation.js'

export default function GraphLegend({ clusters, activeClusters, onToggle }) {
  const [hoveredClusterId, setHoveredClusterId] = useState(null)
  const hoveredCluster = clusters.find((c) => c.cluster_id === hoveredClusterId)

  return (
    <div className="graph-legend">
      <p className="label">Clusters</p>
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
    </div>
  )
}
