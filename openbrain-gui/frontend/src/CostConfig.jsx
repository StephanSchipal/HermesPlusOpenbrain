const CONFIG_NOTES = {
  'model.default': 'Opus costs roughly 2.5× Sonnet per token.',
  'compression.threshold': 'Fraction of the model context window before compaction runs.',
  'compression.threshold_tokens': 'Absolute token cap before compaction. Unset means only the fraction applies.',
  'tool_output.max_bytes': 'Cap on one tool result. 50,000 bytes is roughly 12k tokens.',
  'prompt_caching.cache_ttl': '5m writes cost 1.25× base; 1h writes cost 2×.',
  'agent.max_turns': 'Upper bound on tool-calling turns per task.',
  'agent.disabled_toolsets': 'Every enabled tool ships its JSON schema in each request prefix.',
  'sessions.auto_prune': 'Old sessions are deleted from state.db when on.',
  'sessions.retention_days': 'How long session history survives pruning.',
}

export default function CostConfig({ tools, promptBudget, config }) {
  return (
    <div className="cost-config">
      <section className="cost-table-block">
        <h4>Top tools</h4>
        {tools?.token_attribution_available === false && (
          <p className="cost-note">
            Call counts only — Hermes stores no per-message token counts, so tokens cannot be
            attributed to individual tools.
          </p>
        )}
        <table className="cost-table">
          <thead><tr><th>Tool</th><th>Calls</th></tr></thead>
          <tbody>
            {(tools?.tools || []).map((t) => (
              <tr key={t.tool_name}><td>{t.tool_name}</td><td>{t.calls}</td></tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="cost-table-block">
        <h4>Prompt budget</h4>
        <table className="cost-table">
          <thead>
            <tr><th>Platform</th><th>Sessions</th><th>Avg prompt</th><th>Largest</th></tr>
          </thead>
          <tbody>
            {(promptBudget || []).map((p, i) => (
              <tr key={p.platform || i}>
                <td>{p.platform || <em className="cost-note">(pruned)</em>}</td>
                <td>{p.sessions}</td>
                <td>{Math.round(p.avg_system_prompt_chars).toLocaleString()} chars</td>
                <td>{p.max_system_prompt_chars?.toLocaleString()} chars</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="cost-table-block">
        <h4>Config</h4>
        <table className="cost-table">
          <thead><tr><th>Key</th><th>Value</th><th>What it costs</th></tr></thead>
          <tbody>
            {Object.entries(config || {}).map(([key, value]) => (
              <tr key={key}>
                <td><code>{key}</code></td>
                <td>{JSON.stringify(value)}</td>
                <td className="cost-note">{CONFIG_NOTES[key] || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
