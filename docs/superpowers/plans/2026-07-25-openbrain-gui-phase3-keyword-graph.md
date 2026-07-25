# OpenBrain Web GUI — Phase 3 — Keyword Graph — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Keyword Graph" view to `openbrain-gui` — a graphify-inspired, clustered map of the
corpus's keywords, powered entirely by the existing `cluster_captures` MCP tool.

**Architecture:** One new backend module (`app/graph.py`, pure aggregation functions) and one new
route (`GET /api/graph`) combine `list_recent()` and `cluster_captures()` into a keyword-centric
payload. Three new frontend files (`graphSimulation.js`, `GraphLegend.jsx`, `KeywordGraph.jsx`) plus
a small `App.jsx` refactor (a three-way `view` state replacing the boolean `showDeleteLog`) render
it as a new toggle view, exactly like the existing "Show delete log".

**Tech Stack:** Same as the rest of `openbrain-gui` (FastAPI backend, plain JS/JSX React frontend,
no TypeScript) plus three small D3 sub-packages: `d3-force`, `d3-zoom`, `d3-selection`.

---

### Task 1: Backend — `app/graph.py`

**Files:**
- Create: `openbrain-gui/backend/app/graph.py`
- Test: `openbrain-gui/backend/tests/test_graph.py`

- [ ] **Step 1: Write the failing tests** **[repo]**

Create `openbrain-gui/backend/tests/test_graph.py`:

```python
# tests/test_graph.py
from app.graph import build_keyword_graph
from app.subject_line import make_subject_line

def test_build_keyword_graph_basic_two_clusters():
    captures = [
        {"id": "a", "keywords": ["claude", "ai"]},
        {"id": "b", "keywords": ["claude"]},
        {"id": "c", "keywords": ["wall street"]},
    ]
    clusters = [
        {"cluster_id": 0, "size": 2, "members": [
            {"id": "a", "summary": "Claude does things", "central": True},
            {"id": "b", "summary": "Claude again", "central": False},
        ]},
        {"cluster_id": 1, "size": 1, "members": [
            {"id": "c", "summary": "Banks and markets", "central": True},
        ]},
    ]
    result = build_keyword_graph(captures, clusters)

    keywords_by_name = {k["keyword"]: k for k in result["keywords"]}
    assert keywords_by_name["claude"]["count"] == 2
    assert keywords_by_name["claude"]["cluster_id"] == 0
    assert {c["id"] for c in keywords_by_name["claude"]["captures"]} == {"a", "b"}
    assert keywords_by_name["ai"]["count"] == 1
    assert keywords_by_name["ai"]["cluster_id"] == 0
    assert keywords_by_name["wall street"]["count"] == 1
    assert keywords_by_name["wall street"]["cluster_id"] == 1

    clusters_by_id = {c["cluster_id"]: c for c in result["clusters"]}
    assert clusters_by_id[0]["label"] == "claude"
    assert clusters_by_id[0]["size"] == 2
    assert clusters_by_id[1]["label"] == "wall street"

def test_build_keyword_graph_tie_broken_by_lowest_cluster_id():
    # "shared" occurs once in cluster 1 and once in cluster 0 -- tied count,
    # lowest cluster_id must win the keyword's dominant-cluster assignment.
    captures = [
        {"id": "a", "keywords": ["shared"]},
        {"id": "b", "keywords": ["shared"]},
    ]
    clusters = [
        {"cluster_id": 1, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
        {"cluster_id": 0, "size": 1, "members": [{"id": "b", "summary": "y", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    shared = next(k for k in result["keywords"] if k["keyword"] == "shared")
    assert shared["cluster_id"] == 0

def test_build_keyword_graph_label_tie_broken_alphabetically():
    captures = [{"id": "a", "keywords": ["zebra", "apple"]}]
    clusters = [
        {"cluster_id": 0, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["clusters"][0]["label"] == "apple"

def test_build_keyword_graph_skips_capture_missing_from_either_input():
    # "b" is in captures but absent from every cluster's members -- simulates
    # the two-MCP-call race documented in graph.py; must be skipped, not raise.
    captures = [
        {"id": "a", "keywords": ["claude"]},
        {"id": "b", "keywords": ["ghost"]},
    ]
    clusters = [
        {"cluster_id": 0, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert [k["keyword"] for k in result["keywords"]] == ["claude"]

def test_build_keyword_graph_central_flag_preserved_on_both_sides():
    captures = [{"id": "a", "keywords": ["claude"]}]
    clusters = [
        {"cluster_id": 0, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["keywords"][0]["captures"][0]["central"] is True
    assert result["clusters"][0]["captures"][0]["central"] is True

def test_build_keyword_graph_uses_subject_line_not_raw_summary():
    long_summary = " ".join(f"word{i}" for i in range(20))
    captures = [{"id": "a", "keywords": ["claude"]}]
    clusters = [
        {"cluster_id": 0, "size": 1,
         "members": [{"id": "a", "summary": long_summary, "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["clusters"][0]["captures"][0]["subject_line"] == make_subject_line(long_summary)

def test_build_keyword_graph_label_falls_back_when_cluster_has_no_keywords():
    captures = [{"id": "a", "keywords": []}]
    clusters = [
        {"cluster_id": 3, "size": 1, "members": [{"id": "a", "summary": "x", "central": True}]},
    ]
    result = build_keyword_graph(captures, clusters)
    assert result["clusters"][0]["label"] == "cluster 3"
```

- [ ] **Step 2: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.graph'`

- [ ] **Step 3: Write the implementation** **[repo]**

Create `openbrain-gui/backend/app/graph.py`:

```python
# app/graph.py
"""Builds the /api/graph payload: combines list_recent()'s per-capture
keywords with cluster_captures()'s cluster membership into a
keyword-centric view -- one entry per keyword (its dominant cluster,
total count, and the captures that contain it) and one entry per
cluster (a deterministic label, size, and its captures)."""
from app.subject_line import make_subject_line

def build_keyword_graph(captures: list[dict], clusters: list[dict]) -> dict:
    """captures: list_recent()'s output ([{"id", "keywords", ...}, ...]).
    clusters: cluster_captures()'s "clusters" list
        ([{"cluster_id", "size", "members": [{"id", "summary", "central"}]}]).
    Captures present in one input but not the other (a race between the two
    MCP calls -- see mcp_client.py's module docstring for the general
    pattern) are skipped rather than raising."""
    member_info: dict[str, dict] = {}
    for cluster in clusters:
        for member in cluster["members"]:
            member_info[member["id"]] = {
                "cluster_id": cluster["cluster_id"],
                "central": member["central"],
                "subject_line": make_subject_line(member["summary"]),
            }

    capture_keywords = {c["id"]: c["keywords"] for c in captures}

    # keyword -> cluster_id -> occurrence count (dominant-cluster assignment)
    keyword_cluster_counts: dict[str, dict[int, int]] = {}
    # keyword -> captures containing it (not just captures in its cluster)
    keyword_captures: dict[str, list[dict]] = {}

    for capture_id, keywords in capture_keywords.items():
        info = member_info.get(capture_id)
        if info is None:
            continue
        for keyword in keywords:
            counts = keyword_cluster_counts.setdefault(keyword, {})
            counts[info["cluster_id"]] = counts.get(info["cluster_id"], 0) + 1
            keyword_captures.setdefault(keyword, []).append({
                "id": capture_id,
                "subject_line": info["subject_line"],
                "central": info["central"],
            })

    keywords_out = []
    for keyword, counts in keyword_cluster_counts.items():
        # Highest count wins; ties broken by the lowest cluster_id.
        dominant_cluster_id = max(counts, key=lambda cid: (counts[cid], -cid))
        keywords_out.append({
            "keyword": keyword,
            "count": sum(counts.values()),
            "cluster_id": dominant_cluster_id,
            "captures": keyword_captures[keyword],
        })

    clusters_out = []
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        label_counts: dict[str, int] = {}
        for member in cluster["members"]:
            for keyword in capture_keywords.get(member["id"], []):
                label_counts[keyword] = label_counts.get(keyword, 0) + 1
        if label_counts:
            # Highest count wins; ties broken alphabetically.
            label = min(label_counts, key=lambda kw: (-label_counts[kw], kw))
        else:
            label = f"cluster {cluster_id}"
        clusters_out.append({
            "cluster_id": cluster_id,
            "label": label,
            "size": cluster["size"],
            "captures": [
                {
                    "id": m["id"],
                    "subject_line": member_info[m["id"]]["subject_line"],
                    "central": m["central"],
                }
                for m in cluster["members"]
            ],
        })

    return {"clusters": clusters_out, "keywords": keywords_out}
```

- [ ] **Step 4: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_graph.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/graph.py openbrain-gui/backend/tests/test_graph.py
git commit -m "feat(gui-backend): add build_keyword_graph aggregation"
```

---

### Task 2: Backend — `GET /api/graph` route

**Files:**
- Modify: `openbrain-gui/backend/app/config.py`
- Modify: `openbrain-gui/backend/app/routes.py`
- Test: `openbrain-gui/backend/tests/test_routes.py`

- [ ] **Step 1: Add `GRAPH_MAX_CAPTURES` to config.py** **[repo]**

Modify `openbrain-gui/backend/app/config.py` — add one line after `DEFAULT_DELETE_LOG_LIMIT`:

```python
GUI_DB_PATH = os.environ.get("GUI_DB_PATH", "gui.db")
DEFAULT_SEARCH_K = 25
DEFAULT_DELETE_LOG_LIMIT = 50
GRAPH_MAX_CAPTURES = 100_000
```

- [ ] **Step 2: Write the failing tests** **[repo]**

Modify `openbrain-gui/backend/tests/test_routes.py` — add at the end of the file:

```python
def test_get_graph_builds_keyword_and_cluster_data(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            assert arguments == {"n": 100_000}
            return _list_result([
                {"id": "a", "keywords": ["claude"]},
                {"id": "b", "keywords": ["claude", "ai"]},
            ])
        if name == "cluster_captures":
            assert arguments == {"k": None}
            return _dict_result({"k": 1, "clusters": [
                {"cluster_id": 0, "size": 2, "members": [
                    {"id": "a", "summary": "Claude does things", "central": True},
                    {"id": "b", "summary": "Claude and AI", "central": False},
                ]},
            ]})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["clusters"][0]["label"] == "claude"
    keywords_by_name = {k["keyword"]: k for k in body["keywords"]}
    assert keywords_by_name["claude"]["count"] == 2
    assert keywords_by_name["ai"]["count"] == 1

def test_get_graph_returns_not_enough_captures(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        if name == "list_recent":
            return _list_result([{"id": "a", "keywords": []}, {"id": "b", "keywords": []}])
        if name == "cluster_captures":
            return _dict_result({"error": "need at least 4 captures to cluster, have 2"})
        raise AssertionError(f"unexpected tool call: {name}")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    assert resp.json() == {"error": "not_enough_captures", "count": 2}

def test_get_graph_mcp_failure_returns_502(client, monkeypatch):
    async def fake_call_tool(name, arguments):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mcp_client_module, "call_tool", fake_call_tool)
    resp = client.get("/api/graph")
    assert resp.status_code == 502
```

- [ ] **Step 3: Run tests to verify they fail** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/test_routes.py -v -k get_graph`
Expected: FAIL — `404` (no `/api/graph` route yet)

- [ ] **Step 4: Add the route** **[repo]**

Modify `openbrain-gui/backend/app/routes.py` — update the two import lines at the top:

```python
from app import mcp_client, prompts_store, delete_log_store, subject_line, graph
from app.mcp_client import OpenBrainMCPError
from app.config import DEFAULT_SEARCH_K, DEFAULT_DELETE_LOG_LIMIT, GRAPH_MAX_CAPTURES
```

Then add this route at the end of the file, after `get_delete_log`:

```python
@router.get("/graph")
async def get_graph():
    captures_result = await _call("list_recent", {"n": GRAPH_MAX_CAPTURES})
    try:
        captures = mcp_client.parse_list_result(captures_result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    cluster_result = await _call("cluster_captures", {"k": None})
    try:
        cluster_data = mcp_client.parse_dict_result(cluster_result)
    except OpenBrainMCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if "error" in cluster_data:
        return {"error": "not_enough_captures", "count": len(captures)}

    return graph.build_keyword_graph(captures, cluster_data["clusters"])
```

- [ ] **Step 5: Run tests to verify they pass** **[repo]**

Run: `cd openbrain-gui/backend && python -m pytest tests/ -v`
Expected: `35 passed` (25 existing + 7 from Task 1 + 3 from this task)

- [ ] **Step 6: Commit** **[repo]**

```bash
git add openbrain-gui/backend/app/config.py openbrain-gui/backend/app/routes.py \
        openbrain-gui/backend/tests/test_routes.py
git commit -m "feat(gui-backend): add GET /api/graph route"
```

---

### Task 3: Frontend — D3 dependencies and `api.js`

**Files:**
- Modify: `openbrain-gui/frontend/package.json`
- Modify: `openbrain-gui/frontend/src/api.js`

- [ ] **Step 1: Add dependencies to package.json** **[repo]**

Modify `openbrain-gui/frontend/package.json` — update `"dependencies"`:

```json
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "d3-force": "^3.0.0",
    "d3-selection": "^3.0.0",
    "d3-zoom": "^3.0.0"
  },
```

- [ ] **Step 2: Install** **[repo]**

```bash
cd openbrain-gui/frontend && npm install
```

- [ ] **Step 3: Add `getGraph` to api.js** **[repo]**

Modify `openbrain-gui/frontend/src/api.js` — add one line inside the `api` object, after `getDeleteLog`:

```javascript
  getDeleteLog: (limit) => request(`/delete-log${limit ? `?limit=${limit}` : ''}`),
  getGraph: () => request('/graph'),
```

- [ ] **Step 4: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/package.json openbrain-gui/frontend/package-lock.json \
        openbrain-gui/frontend/src/api.js
git commit -m "feat(gui-frontend): add d3 dependencies and getGraph API call"
```

---

### Task 4: Frontend — `graphSimulation.js`

**Files:**
- Create: `openbrain-gui/frontend/src/graphSimulation.js`

- [ ] **Step 1: Create the file** **[repo]**

Create `openbrain-gui/frontend/src/graphSimulation.js`:

```javascript
import { select } from 'd3-selection'
import { zoom } from 'd3-zoom'
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
```

- [ ] **Step 2: Sanity-check the file parses** **[repo]**

```bash
cd openbrain-gui/frontend && node --input-type=module -e "$(cat src/graphSimulation.js) console.log('ok')"
```

Expected: `ok` (proves the syntax and imports resolve; there's no automated test suite for the
frontend per the established Phase 1 precedent — this is just a parse/import smoke check).

- [ ] **Step 3: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/graphSimulation.js
git commit -m "feat(gui-frontend): add d3-force/d3-zoom keyword bubble layout"
```

---

### Task 5: Frontend — `GraphLegend.jsx`

**Files:**
- Create: `openbrain-gui/frontend/src/GraphLegend.jsx`

- [ ] **Step 1: Create the file** **[repo]**

Create `openbrain-gui/frontend/src/GraphLegend.jsx`:

```jsx
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
            {hoveredCluster.captures.slice(0, 3).map((c) => (
              <li key={c.id}>{c.central ? '★ ' : ''}{c.subject_line}</li>
            ))}
            {hoveredCluster.captures.length > 3 && (
              <li>+{hoveredCluster.captures.length - 3} more</li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/GraphLegend.jsx
git commit -m "feat(gui-frontend): add GraphLegend cluster sidebar with hover tooltip"
```

---

### Task 6: Frontend — `KeywordGraph.jsx` and CSS

**Files:**
- Create: `openbrain-gui/frontend/src/KeywordGraph.jsx`
- Modify: `openbrain-gui/frontend/src/index.css`

- [ ] **Step 1: Create KeywordGraph.jsx** **[repo]**

Create `openbrain-gui/frontend/src/KeywordGraph.jsx`:

```jsx
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

  const load = () => {
    setError(null)
    api.getGraph()
      .then((result) => {
        setData(result)
        setActiveClusters(
          result.clusters ? new Set(result.clusters.map((c) => c.cluster_id)) : null,
        )
      })
      .catch((err) => setError(err.message))
  }

  useEffect(load, [])

  useEffect(() => {
    if (!data || data.error || !svgRef.current) return
    graphApiRef.current = renderGraph(svgRef.current, {
      keywords: data.keywords,
      clusterCount: data.clusters.length,
      width: WIDTH,
      height: HEIGHT,
      onNodeClick: onKeywordClick,
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
```

- [ ] **Step 2: Add CSS** **[repo]**

Modify `openbrain-gui/frontend/src/index.css` — add at the end of the file:

```css
.keyword-graph { display: flex; flex-direction: column; gap: 8px; }
.keyword-graph-toolbar { display: flex; gap: 8px; align-items: center; }
.keyword-graph-toolbar .keyword-filter { flex: 1; }
.keyword-graph-body { display: flex; gap: 16px; }
.keyword-graph-canvas { flex: 3; position: relative; }
.keyword-graph-canvas svg {
  background: var(--bg); border: 1px solid var(--border); border-radius: 6px; display: block;
}
.graph-zoom-controls {
  position: absolute; top: 8px; right: 8px; display: flex; flex-direction: column; gap: 4px;
}
.graph-zoom-controls button {
  width: 28px; height: 28px; padding: 0; font-size: 16px; line-height: 1;
  background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 4px;
}
.graph-tooltip {
  position: absolute; top: 8px; left: 8px; max-width: 260px;
  background: #111; color: #fff; border: 1px solid var(--accent); border-radius: 6px;
  padding: 8px; font-size: 12px; pointer-events: none; z-index: 5;
}
.graph-tooltip ul { margin: 4px 0 0; padding-left: 16px; }
.graph-legend { flex: 1; border-left: 1px solid var(--border); padding-left: 12px; position: relative; }
.graph-legend-row { display: flex; align-items: center; gap: 6px; cursor: pointer; margin-bottom: 6px; }
```

- [ ] **Step 3: Verify the frontend still builds** **[repo]**

```bash
cd openbrain-gui/frontend && npm run build
```

Expected: builds successfully (this only proves the JSX/imports resolve — `KeywordGraph` isn't
wired into the app yet, that's Task 7).

- [ ] **Step 4: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/KeywordGraph.jsx openbrain-gui/frontend/src/index.css
git commit -m "feat(gui-frontend): add KeywordGraph view component"
```

---

### Task 7: Frontend — wire into `App.jsx`

**Files:**
- Modify: `openbrain-gui/frontend/src/App.jsx`

- [ ] **Step 1: Add the import** **[repo]**

Modify `openbrain-gui/frontend/src/App.jsx` — add after the `SummaryPopup` import:

```javascript
import SummaryPopup from './SummaryPopup.jsx'
import KeywordGraph from './KeywordGraph.jsx'
```

- [ ] **Step 2: Replace `showDeleteLog` with a three-way `view` state** **[repo]**

Modify `openbrain-gui/frontend/src/App.jsx` — replace this line:

```javascript
  const [showDeleteLog, setShowDeleteLog] = useState(false)
```

with:

```javascript
  const [view, setView] = useState('results') // 'results' | 'deleteLog' | 'graph'
```

- [ ] **Step 3: Update `handleSearch`** **[repo]**

Modify `openbrain-gui/frontend/src/App.jsx` — replace this line inside `handleSearch`:

```javascript
    setShowDeleteLog(false)
```

with:

```javascript
    setView('results')
```

- [ ] **Step 4: Update `canLoadMore`** **[repo]**

Modify `openbrain-gui/frontend/src/App.jsx` — replace this line:

```javascript
  const canLoadMore = !showDeleteLog && rows.length > 0 && rows.length >= searchK
```

with:

```javascript
  const canLoadMore = view === 'results' && rows.length > 0 && rows.length >= searchK
```

- [ ] **Step 5: Replace the grid-actions and view-switch JSX** **[repo]**

Modify `openbrain-gui/frontend/src/App.jsx` — replace this block:

```jsx
        <button onClick={() => setShowDeleteLog((v) => !v)}>
          {showDeleteLog ? 'Back to results' : 'Show delete log'}
        </button>
      </div>

      {showDeleteLog ? (
        <DeleteLogView />
      ) : searching ? (
        <p className="grid-empty">Searching…</p>
      ) : (
        <ResultGrid rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
      )}
```

with:

```jsx
        <button onClick={() => setView((v) => (v === 'deleteLog' ? 'results' : 'deleteLog'))}>
          {view === 'deleteLog' ? 'Back to results' : 'Show delete log'}
        </button>
        <button onClick={() => setView((v) => (v === 'graph' ? 'results' : 'graph'))}>
          {view === 'graph' ? 'Back to results' : 'Show keyword graph'}
        </button>
      </div>

      {view === 'deleteLog' ? (
        <DeleteLogView />
      ) : view === 'graph' ? (
        <KeywordGraph onKeywordClick={handleKeywordClick} />
      ) : searching ? (
        <p className="grid-empty">Searching…</p>
      ) : (
        <ResultGrid rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
      )}
```

- [ ] **Step 6: Verify the frontend builds** **[repo]**

```bash
cd openbrain-gui/frontend && npm run build
```

Expected: builds successfully.

- [ ] **Step 7: Commit** **[repo]**

```bash
git add openbrain-gui/frontend/src/App.jsx
git commit -m "feat(gui-frontend): wire KeywordGraph into a three-way view toggle"
```

---

### Task 8: Manual end-to-end verification (local dev stack)

**Files:** none (verification only)

- [ ] **Step 1: Run backend + frontend dev servers against a local openbrain-mcp** **[repo, needs a local openbrain-mcp]**

Follow `README.md`'s "Running it locally" section for `openbrain-gui` (same three-terminal setup
used for every prior GUI feature): a local `openbrain-mcp` with its port published via
`deploy/docker-compose.override.yml`, the GUI backend pointed at it, and `npm run dev` for the
frontend. The corpus needs **at least 4 real captures** with varied keywords to exercise
clustering meaningfully — reuse whatever is already in the local `openbrain-test-db` / save a few
via Claude Desktop/Code first if it's empty.

- [ ] **Step 2: Manually exercise every Phase 3 interaction in a browser** **[repo]**

Open the frontend dev URL and verify:

1. Clicking **Show keyword graph** swaps the grid for the graph view; **Show delete log** and
   **Back to results** still work correctly and the three views never show simultaneously.
2. Keyword bubbles render, sized by frequency, colored by cluster, with white/dark-halo labels
   readable against every bubble color.
3. Scroll-wheel and the on-screen +/− buttons zoom in and out; dragging pans the canvas.
4. Hovering a keyword bubble shows a tooltip listing captures that contain it.
5. Hovering a row in the cluster legend shows a tooltip listing every capture in that cluster,
   with `central` captures starred.
6. Typing in the filter box above the graph dims/hides non-matching bubbles instantly (check the
   Network tab: no new request fires per keystroke).
7. Unchecking a cluster in the legend hides its bubbles instantly, no new request.
8. Clicking a keyword bubble inserts it into the still-visible prompt textarea above, identically
   to clicking a chip in the existing keyword-filter list.
9. Clicking **Refresh** re-fetches and re-lays-out the graph.
10. Temporarily point `GUI_DB_PATH`/`OPENBRAIN_MCP_URL` at an empty or near-empty test corpus (or
    just note the behavior if the local one currently has fewer than 4 captures) and confirm the
    "Not enough captures yet..." message renders instead of a crash or blank canvas.

- [ ] **Step 3: Tear down** **[repo]**

Stop all three terminals (Ctrl+C). If not needed again immediately:
`cd deploy && docker compose -f docker-compose.openbrain.yml down && rm docker-compose.override.yml`

---

### Task 9: Update project documentation

**Files:**
- Modify: `README.md`
- Modify: `OpenbrainAddition.md`

- [ ] **Step 1: Update README.md's heading and intro** **[repo]**

Modify `README.md` — replace:

```markdown
## OpenBrain Web GUI (Phase 1)

A single-user React + FastAPI web GUI for browsing, searching, editing, and deleting captures — a
friendlier alternative to querying via Claude Desktop/Code or WhatsApp. Phase 1 of a three-phase
plan (Phase 2: dynamic word cloud + AND/OR keyword search; Phase 3: surfacing clustering/
classification in the GUI — both future work, not yet started).

Full details:
- Design spec — [`docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`](docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md)
- Implementation plan — [`docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md`](docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md)
```

with:

```markdown
## OpenBrain Web GUI

A single-user React + FastAPI web GUI for browsing, searching, editing, and deleting captures — a
friendlier alternative to querying via Claude Desktop/Code or WhatsApp. Phase 1 (search/browse/
change/delete, saved prompts, delete log) and Phase 3 (a keyword graph — see below) are both live.
**Phase 2 (dynamic word cloud + AND/OR keyword search) was explicitly skipped** in favor of Phase
3's graph.

Full details:
- Phase 1 design spec — [`docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`](docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md)
- Phase 1 implementation plan — [`docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md`](docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md)
- Phase 3 design spec — [`docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md`](docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md)
- Phase 3 implementation plan — [`docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md`](docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md)
```

- [ ] **Step 2: Add a Keyword graph paragraph to README.md** **[repo]**

Modify `README.md` — insert this new paragraph right after the existing `**Status:**` paragraph
(the one ending "...a deliberately accepted, revisitable tradeoff for this personal, single-VPS
deployment.") and before the `### Running it locally` heading:

```markdown
**Keyword graph (Phase 3):** a "Show keyword graph" toggle (next to "Show delete log") renders
every keyword as a bubble, sized by frequency and colored by an automatically-detected thematic
cluster — powered entirely by the existing `cluster_captures` MCP tool, no new `openbrain-mcp`
capability needed. Hovering a bubble or a cluster in the legend shows the captures behind it
(central/most-representative ones starred); clicking a bubble inserts that keyword into the search
prompt, same as the existing keyword-filter list. Pan/zoom via scroll, drag, or the on-screen
+/− buttons.
```

- [ ] **Step 3: Update OpenbrainAddition.md** **[repo]**

Modify `OpenbrainAddition.md`'s §8 — replace the end of item 5 (currently reading "...Links. Phase
2 (Wordcloud, AND/OR-Keyword-Suche) und Phase 3 (Clustering/Klassifikation in der GUI) sind bewusst
nicht Teil dieser Phase — siehe `planGUI.md`.") with:

```markdown
   Links. Phase 2 (Wordcloud, AND/OR-Keyword-Suche) wurde übersprungen —
   siehe Punkt 6.

6. ✅ **Web-GUI Phase 3 — Keyword-Graph** (`openbrain-gui`) — eine neue
   "Show keyword graph"-Ansicht (neben "Show delete log") zeigt jedes
   Keyword als Bubble, Größe = Häufigkeit, Farbe = automatisch erkanntes
   Themen-Cluster — komplett über das bestehende `cluster_captures`-Tool,
   keine neue `openbrain-mcp`-Fähigkeit nötig. Hover auf einer Bubble oder
   einer Cluster-Zeile in der Legende zeigt die zugehörigen Einträge
   (zentrale/repräsentativste mit ★ markiert); Klick auf eine Bubble fügt
   das Keyword ins Suchfeld ein, wie bei der bestehenden Keyword-Liste.
   Zoom/Pan per Scrollrad, Ziehen oder +/−-Buttons (`d3-force` fürs
   Cluster-Layout, `d3-zoom` fürs Zoomen — zwei kleine, gezielte
   Zusatzpakete). Phase 2 (Wordcloud, AND/OR-Keyword-Suche) wurde bewusst
   übersprungen. Spec:
   [`docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md`](docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md`](docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md).
```

Then update the paragraph right after it from:

```markdown
Damit sind alle 4 ursprünglich geplanten MCP-Fähigkeiten sowie Phase 1 der
Web-GUI umgesetzt und deployed. Details zu jeder einzelnen siehe die
jeweiligen Spec-/Plan-Dokumente unter `docs/superpowers/`.
```

to:

```markdown
Damit sind alle 4 ursprünglich geplanten MCP-Fähigkeiten sowie Phase 1 und
Phase 3 der Web-GUI umgesetzt und deployed (Phase 2 bewusst übersprungen).
Details zu jeder einzelnen siehe die jeweiligen Spec-/Plan-Dokumente unter
`docs/superpowers/`.
```

- [ ] **Step 4: Commit** **[repo]**

```bash
git add README.md OpenbrainAddition.md
git commit -m "docs: document the Phase 3 keyword graph view"
```

---

### Task 10: Deploy to production and verify live

**Files:** none (deployment only)

- [ ] **Step 1: Push to GitHub** **[repo]**

```bash
git push origin main
```

- [ ] **Step 2: Pull and rebuild on the VPS**

```bash
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain && git pull --ff-only origin main"
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain/deploy && docker compose -f docker-compose.openbrain.yml up -d --build openbrain-gui"
```

- [ ] **Step 3: Verify container health**

```bash
ssh root@srv1608402.hstgr.cloud "docker compose -f /root/HermesPlusOpenbrain/deploy/docker-compose.openbrain.yml ps"
```

Expected: `deploy-openbrain-gui-1` shows `healthy` (existing `/health` check from the prior UX-fixes
round).

- [ ] **Step 4: Manually verify against the real production corpus**

Open `https://gui.<vps-host>.hstgr.cloud`, log in with the Traefik basic-auth credentials, click
**Show keyword graph**, and repeat the key checks from Task 8 Step 2 (bubbles render and cluster
correctly, zoom/pan, hover tooltips, filter box, cluster toggles, click-to-search) against the real
corpus.

---
