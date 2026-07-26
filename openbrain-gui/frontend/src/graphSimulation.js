import { select } from 'd3-selection'
import { zoom, zoomIdentity } from 'd3-zoom'
import { forceSimulation, forceX, forceY, forceCollide, forceManyBody } from 'd3-force'

// A fixed, cycling color palette -- not hardcoded to however many clusters
// the corpus happens to have today (design spec section 3).
const CLUSTER_COLORS = [
  '#6c8cff', '#ff9f43', '#2ecc71', '#e74c3c', '#9b59b6',
  '#1abc9c', '#f1c40f', '#e67e22', '#3498db', '#95a5a6',
]

export function clusterColor(clusterId) {
  return CLUSTER_COLORS[clusterId % CLUSTER_COLORS.length]
}

function clusterCentroid(clusterId, clusterCount, width, height) {
  if (clusterCount <= 1) return { x: width / 2, y: height / 2 }
  const angle = (2 * Math.PI * clusterId) / clusterCount
  const radius = Math.min(width, height) * 0.3
  return {
    x: width / 2 + radius * Math.cos(angle),
    y: height / 2 + radius * Math.sin(angle),
  }
}

// Renders keyword bubbles clustered by color (no edges -- design spec
// section 3, "Node relationships") with d3-force for layout and d3-zoom
// for pan/zoom. Returns an imperative handle for the calling component
// (KeywordGraph.jsx) to drive zoom buttons and client-side filtering
// without re-running the simulation.
export function renderGraph(svgEl, { keywords, clusterCount, width, height, onNodeClick, onNodeHover }) {
  const nodes = keywords.map((k) => ({ ...k, radius: 6 + Math.sqrt(k.count) * 6 }))

  const svg = select(svgEl)
  svg.selectAll('*').remove()
  // Re-rendering (e.g. on Refresh) resets the visible transform to identity
  // by rebuilding zoomLayer from scratch -- also reset d3-zoom's own stored
  // __zoom state on the node, otherwise the next zoomIn/zoomOut/scroll/drag
  // computes its delta from a stale prior transform and the view jumps.
  svg.property('__zoom', zoomIdentity)

  const zoomLayer = svg.append('g').attr('class', 'zoom-layer')

  const zoomBehavior = zoom()
    .scaleExtent([0.3, 5])
    .on('zoom', (event) => zoomLayer.attr('transform', event.transform))
  svg.call(zoomBehavior)

  const circles = zoomLayer.selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', (d) => d.radius)
    .attr('fill', (d) => clusterColor(d.cluster_id))
    .style('cursor', 'pointer')
    .on('click', (_event, d) => onNodeClick(d.keyword))
    .on('mouseenter', (_event, d) => onNodeHover(d))
    .on('mouseleave', () => onNodeHover(null))

  const labels = zoomLayer.selectAll('text')
    .data(nodes)
    .join('text')
    .text((d) => d.keyword)
    .attr('text-anchor', 'middle')
    .attr('dy', '0.35em')
    .attr('font-size', 11)
    .attr('fill', '#ffffff')
    .attr('stroke', '#000000')
    .attr('stroke-width', 2.5)
    .attr('paint-order', 'stroke')
    .style('pointer-events', 'none')

  const simulation = forceSimulation(nodes)
    .force('x', forceX((d) => clusterCentroid(d.cluster_id, clusterCount, width, height).x).strength(0.15))
    .force('y', forceY((d) => clusterCentroid(d.cluster_id, clusterCount, width, height).y).strength(0.15))
    .force('collide', forceCollide((d) => d.radius + 2))
    .force('charge', forceManyBody().strength(-8))
    .on('tick', () => {
      circles.attr('cx', (d) => d.x).attr('cy', (d) => d.y)
      labels.attr('x', (d) => d.x).attr('y', (d) => d.y)
    })

  return {
    zoomIn: () => svg.transition().call(zoomBehavior.scaleBy, 1.3),
    zoomOut: () => svg.transition().call(zoomBehavior.scaleBy, 1 / 1.3),
    setVisible: (isVisibleFn) => {
      circles.attr('opacity', (d) => (isVisibleFn(d) ? 1 : 0.08))
      labels.attr('opacity', (d) => (isVisibleFn(d) ? 1 : 0.08))
    },
    destroy: () => simulation.stop(),
  }
}
