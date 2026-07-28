export default function PromptBar({
  prompt, onPromptChange, promptTextareaRef,
  savedPrompts, onSelectSavedPrompt,
  onSearch, selectedPromptId,
}) {
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSearch()
    }
  }

  return (
    <div className="prompt-bar">
      <select
        className="prompt-dropdown"
        value={selectedPromptId || ''}
        onChange={(e) => onSelectSavedPrompt(e.target.value)}
      >
        <option value="">Saved prompts…</option>
        {savedPrompts.map((p) => (
          <option key={p.id} value={p.id}>
            {p.text.slice(0, 40)}
          </option>
        ))}
      </select>
      <div className="textarea-wrapper">
        <textarea
          ref={promptTextareaRef}
          className="prompt-textarea"
          rows={3}
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Search prompt…"
          spellCheck={false}
        />
        {prompt && (
          <button
            type="button"
            className="textarea-clear"
            aria-label="Clear prompt"
            onClick={() => onPromptChange('')}
          >
            ✕
          </button>
        )}
      </div>
    </div>
  )
}
