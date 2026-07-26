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
    if (params.n) query.set('n', params.n)
    if (params.source) query.set('source', params.source)
    if (params.date_from) query.set('date_from', params.date_from)
    if (params.date_to) query.set('date_to', params.date_to)
    for (const kw of params.keywords || []) query.append('keywords', kw)
    if (params.keyword_mode) query.set('keyword_mode', params.keyword_mode)
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
  getGraph: () => request('/graph'),
}
