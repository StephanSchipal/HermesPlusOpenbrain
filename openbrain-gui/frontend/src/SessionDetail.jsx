import { useEffect, useState } from 'react'
import { api } from './api'
import { usd, tokens } from './format'

export default function SessionDetail({ sessionId, onClose }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [showPrompt, setShowPrompt] = useState(false)

  useEffect(() => {
    setDetail(null); setError(null); setShowPrompt(false)
    api.getCostSession(sessionId).then(setDetail).catch((e) => setError(e.message))
  }, [sessionId])

  return (
    <div className="session-detail">
      <div className="session-detail-header">
        <h4>{detail?.title || sessionId}</h4>
        <button onClick={onClose}>Close</button>
      </div>

      {error && <p className="cost-unavailable">{error}</p>}
      {!detail && !error && <p className="cost-loading">Loading…</p>}

      {detail && (
        <>
          <dl className="session-detail-meta">
            <dt>Platform</dt><dd>{detail.platform || '—'}</dd>
            <dt>Messages</dt><dd>{detail.message_count ?? '—'}</dd>
            <dt>Tool calls</dt><dd>{detail.tool_call_count ?? '—'}</dd>
            <dt>cwd</dt><dd>{detail.cwd || '—'}</dd>
            <dt>Branch</dt><dd>{detail.git_branch || '—'}</dd>
            <dt>Profile</dt><dd>{detail.profile_name || '—'}</dd>
            <dt>Compression fallbacks</dt><dd>{detail.compression_fallback_streak ?? 0}</dd>
            <dt>Compression error</dt><dd>{detail.compression_failure_error || 'none'}</dd>
            <dt>System prompt</dt>
            <dd>
              {detail.system_prompt_chars.toLocaleString()} chars
              {detail.system_prompt_chars > 0 && (
                <> <button className="link-button" onClick={() => setShowPrompt((v) => !v)}>
                  {showPrompt ? 'hide' : 'show'}
                </button></>
              )}
            </dd>
          </dl>

          {showPrompt && <pre className="session-prompt">{detail.system_prompt}</pre>}

          <table className="cost-table">
            <thead>
              <tr>
                <th>Model</th><th>Task</th><th>Calls</th><th>In</th><th>Out</th>
                <th>Cache read</th><th>Cache write</th><th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {detail.models.map((m) => (
                <tr key={`${m.model}-${m.task}`}>
                  <td>{m.model}</td>
                  <td>{m.task || '—'}</td>
                  <td>{m.api_calls}</td>
                  <td>{tokens(m.input_tokens)}</td>
                  <td>{tokens(m.output_tokens)}</td>
                  <td>{tokens(m.cache_read_tokens)}</td>
                  <td>{tokens(m.cache_write_tokens)}</td>
                  <td>{usd(m.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
