export default function PromptBar({
  prompt, onPromptChange, promptTextareaRef,
  savedPrompts, onSelectSavedPrompt,
  onSearch, onSavePrompt, onDeleteSavedPrompt, selectedPromptId,
}) {
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
      <textarea
        ref={promptTextareaRef}
        className="prompt-textarea"
        rows={3}
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder="Search prompt…"
        spellCheck={false}
      />
      <div className="prompt-actions">
        <button onClick={onSearch}>Search</button>
        <button onClick={onSavePrompt}>Save prompt</button>
        <button onClick={onDeleteSavedPrompt} disabled={!selectedPromptId}>
          Delete prompt
        </button>
      </div>
    </div>
  )
}
