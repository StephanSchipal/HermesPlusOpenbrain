// Kept in sync with costpage.md section 2.2 -- if you change one, change both.
export default function TokenTypesExplainPopup({ onClose }) {
  return (
    <div className="popup-overlay">
      <div className="popup">
        <div className="popup-header-row">
          <h3>Token types, and what they cost</h3>
        </div>

        <div className="popup-explain">
          <p>Every API call carries up to four kinds of tokens:</p>
          <ul>
            <li>
              <strong>Input</strong> — the part of your prompt that wasn't already cached, billed at
              the model's base rate.
            </li>
            <li>
              <strong>Output</strong> — what the model writes back. Output tokens cost more per token
              than input on every model Hermes talks to — replies are the most expensive part of a
              call.
            </li>
            <li>
              <strong>Cache write</strong> — the first time a chunk of context (system prompt, tool
              schemas, conversation history) is sent, it gets cached for reuse. A write costs{' '}
              <em>more</em> than a plain input token, not less — you're paying a premium to store it.
            </li>
            <li>
              <strong>Cache read</strong> — every later turn that reuses that same cached chunk pays
              this instead of the full input price. Priced far below input; this is what makes
              caching worth it.
            </li>
          </ul>

          <h4>The number that actually tracks your bill</h4>
          <p>
            <strong>A cache write costs 12.5× a cache read.</strong> So a high cache{' '}
            <strong>hit rate</strong> looks reassuring, but it isn't what predicts cost — the
            Efficiency panel's <strong>cache-write per call</strong> column is. A session that keeps
            writing large new context every turn (long, changing conversations) costs far more than
            one that mostly re-reads a stable cached prompt, even at an identical hit rate.
          </p>

          <h4>Does reading from cache cost anything?</h4>
          <p>
            Yes — a small amount per read, not zero. But it's the cheapest of the four token types,
            which is the whole point of caching: pay once to write a chunk into the cache, then pay a
            fraction of the price every time it's reused instead of paying full input price again.
          </p>
        </div>

        <div className="popup-actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
