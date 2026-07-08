/**
 * KPI tile: número grande + label + hint opcional.
 *
 * Uso:
 *   <KPI
 *     label="Mensajes hoy"
 *     value="142"
 *     hint="vs ayer +12%"
 *     accent
 *   />
 */

export function KPI({ label, value, hint, accent = false }) {
  return (
    <div className="ds-kpi">
      <div className="ds-kpi-label">{label}</div>
      <div className={`ds-kpi-value ${accent ? 'accent' : ''}`}>{value}</div>
      {hint ? <div className="ds-kpi-hint">{hint}</div> : null}
    </div>
  );
}
