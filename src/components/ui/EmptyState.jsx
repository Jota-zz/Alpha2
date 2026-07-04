/**
 * Componentes de estado para listas / cargas:
 *   - <EmptyState>: cuando una query devuelve datos vacíos.
 *   - <ErrorCard>:  cuando una query falla.
 *   - <Loading>:    spinner / texto de carga simple.
 */

export function EmptyState({ icon, title, children }) {
  return (
    <div className="empty-state">
      {icon ? <div className="empty-state-icon">{icon}</div> : null}
      <div className="empty-state-title">{title}</div>
      {children ? <div className="text-sm text-slate-400 mt-1">{children}</div> : null}
    </div>
  );
}

export function ErrorCard({ error }) {
  let msg = 'Error desconocido';
  let detail = null;
  if (error?.response) {
    msg = `${error.response.status} ${error.response.statusText}`;
    detail =
      typeof error.response.data === 'object'
        ? error.response.data.detail || JSON.stringify(error.response.data)
        : String(error.response.data);
  } else if (error?.message) {
    msg = error.message;
  }
  return (
    <div className="error-card">
      <div className="font-semibold mb-1">⚠ {msg}</div>
      {detail ? (
        <div className="text-xs font-mono opacity-80">{detail}</div>
      ) : null}
    </div>
  );
}

export function Loading({ label = 'Cargando…' }) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon anim-ping-slow inline-block">●</div>
      <div className="empty-state-title">{label}</div>
    </div>
  );
}
