/**
 * Tabs horizontales tipo ".ds-nav" del HTML adjunto: pills redondeadas con
 * el activo en accent verde.
 *
 * Uso:
 *   <Tabs
 *     value={current}
 *     onChange={setCurrent}
 *     tabs={[
 *       { value: 'dispatcher', label: 'Dispatcher', icon: <Timer size={14} /> },
 *       ...
 *     ]}
 *   />
 */

export function Tabs({ value, onChange, tabs }) {
  return (
    <div className="ds-nav">
      {tabs.map((t) => (
        <button
          key={t.value}
          className={`ds-btn ${value === t.value ? 'active' : ''}`}
          onClick={() => onChange(t.value)}
          type="button"
        >
          {t.icon}
          <span>{t.label}</span>
        </button>
      ))}
    </div>
  );
}
