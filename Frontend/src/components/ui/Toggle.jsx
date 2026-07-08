/**
 * Toggle switch ds-toggle (input booleano).
 *
 * Uso:
 *   <Toggle checked={value} onChange={setValue} label="Pausado" />
 */

export function Toggle({ checked, onChange, label, disabled = false }) {
  return (
    <label className="flex items-center gap-3 cursor-pointer select-none">
      <span className="ds-toggle">
        <input
          type="checkbox"
          checked={!!checked}
          onChange={(e) => onChange(e.target.checked)}
          disabled={disabled}
        />
        <span className="ds-toggle-slider" />
      </span>
      {label ? <span className="text-sm text-slate-200">{label}</span> : null}
    </label>
  );
}
