/**
 * TopBar: muestra el título de la página actual (derivado del path) y
 * un badge live con el estado del bot.
 *
 * El badge usa `useBotStatus` que llama /api/bot/status cada 5s vía
 * react-query. Si el bot está pausado o caído, el dot cambia de color.
 */

import { useLocation } from 'react-router-dom';
import { useBotStatus } from '../../hooks/useBotStatus';

const TITLES = {
  '/': 'Panel de control',
  '/argos': 'Dashboard',
  '/configuracion': 'Configuración',
  '/broadcasts': 'Broadcasts',
  '/logs': 'Logs',
};

export function TopBar() {
  const { pathname } = useLocation();
  const title = TITLES[pathname] || 'Alpha Bot';
  const { data: status, isError } = useBotStatus();

  let dotClass = 'bot-status-dot';
  let label = 'En línea';
  if (isError) {
    dotClass += ' dead';
    label = 'Sin conexión';
  } else if (status?.extras?.paused) {
    dotClass += ' paused';
    label = 'Pausado';
  } else if (status && !status.dispatcher?.running) {
    dotClass += ' dead';
    label = 'Dispatcher caído';
  } else if (status && !status.gate?.open) {
    dotClass += ' paused';
    label = 'Fuera de horario';
  }

  return (
    <header className="topbar">
      <div className="topbar-title">{title}</div>

      <div className="flex items-center gap-3">
        {/* Quota tile pequeño */}
        {status?.dispatcher && (
          <div className="text-xs text-slate-400 font-mono">
            cuota:{' '}
            <span className="text-slate-200">
              {status.dispatcher.quota_used_today}/{status.dispatcher.quota_total}
            </span>
          </div>
        )}

        {/* Cola pendiente */}
        {status?.dispatcher && status.dispatcher.pending > 0 && (
          <div className="text-xs text-slate-400 font-mono">
            cola: <span className="text-slate-200">{status.dispatcher.pending}</span>
          </div>
        )}

        {/* Bot status pill */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/5 border border-white/10">
          <div className={dotClass} />
          <span className="text-xs font-medium text-slate-200">{label}</span>
        </div>
      </div>
    </header>
  );
}
