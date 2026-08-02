const BASE = '/api'

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`)
  }
  if (resp.status === 204) return null
  return resp.json()
}

export const api = {
  getStats: () => request('/stats'),
  getKeywords: (filter) => request(`/keywords?filter=${encodeURIComponent(filter || '')}`),
  search: (payload) => request('/search', { method: 'POST', body: JSON.stringify(payload) }),
  getRecent: (params) => {
    const query = new URLSearchParams()
    if (params.n != null) query.set('n', params.n)
    if (params.source) query.set('source', params.source)
    if (params.date_from) query.set('date_from', params.date_from)
    if (params.date_to) query.set('date_to', params.date_to)
    for (const kw of params.keywords || []) query.append('keywords', kw)
    if (params.keyword_mode) query.set('keyword_mode', params.keyword_mode)
    for (const id of params.ids || []) query.append('ids', id)
    return request(`/recent?${query.toString()}`)
  },
  deleteCapture: (id, snapshot) =>
    request(`/captures/${id}/delete`, { method: 'POST', body: JSON.stringify(snapshot) }),
  updateCapture: (id, changes) =>
    request(`/captures/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  getPrompts: () => request('/prompts'),
  savePrompt: (text) => request('/prompts', { method: 'POST', body: JSON.stringify({ text }) }),
  deletePrompt: (id) => request(`/prompts/${id}`, { method: 'DELETE' }),
  getDeleteLog: (limit) => request(`/delete-log${limit ? `?limit=${limit}` : ''}`),
  getGraph: (k) => request(`/graph${k ? `?k=${k}` : ''}`),
  getExternalCosts: () => request('/cost/external'),
  saveExternalCosts: (rows) =>
    request('/cost/external', { method: 'PUT', body: JSON.stringify({ rows }) }),
  deleteExternalCost: (id) => request(`/cost/external/${id}`, { method: 'DELETE' }),
  getFx: () => request('/cost/fx'),
  refreshFx: () => request('/cost/fx/refresh', { method: 'POST' }),
  setFx: (usd_to_eur) =>
    request('/cost/fx', { method: 'PUT', body: JSON.stringify({ usd_to_eur }) }),
  getCostDashboard: (days, limit = 50) => request(`/cost/dashboard?days=${days}&limit=${limit}`),
  getCostSummary: (days) => request(`/cost/summary?days=${days}`),
  getCostSession: (id) => request(`/cost/session/${encodeURIComponent(id)}`),
  getCostConfig: () => request('/cost/config'),
  getCostTimeseries: (days, group) => request(`/cost/timeseries?days=${days}&group=${group}`),
}
