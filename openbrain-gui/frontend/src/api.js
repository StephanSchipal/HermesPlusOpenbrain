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
  search: (query, k) => request('/search', { method: 'POST', body: JSON.stringify({ query, k }) }),
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
