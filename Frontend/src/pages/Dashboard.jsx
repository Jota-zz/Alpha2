/**
 * Dashboard: vista general del estado del bot.
 *
 * Muestra 4 KPIs en grid + 2 cards adicionales:
 *   - Estado del dispatcher (running, cola, quota)
 *   - Ventana operativa (abierto/cerrado, hora de cambio)
 *   - Modo (paused, dry_run, log_level)
 *   - Broadcasts agendados (count + próximo)
 *
 * Pollea `/api/bot/status` cada 5s vía `useBotStatus` (mismo hook que
 * el TopBar, react-query deduplica).
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Clock,
  Megaphone,
  Pause,
  Play,
  Zap,
} from 'lucide-react';
import { useBotStatus } from '../hooks/useBotStatus';
import { pauseBot, resumeBot } from '../api/client';
import { Card, CardTitle } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { KPI } from '../components/ui/KPI';
import { ErrorCard, Loading } from '../components/ui/EmptyState';

function formatDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('es-CO', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatRelative(iso) {
  if (!iso) return null;
  const diffMs = new Date(iso).getTime() - Date.now();
  const diffMin = Math.round(diffMs / 60000);
  if (Math.abs(diffMin) < 60) {
    return diffMin >= 0 ? `en ${diffMin}min` : `hace ${-diffMin}min`;
  }
  const diffH = Math.round(diffMin / 60);
  return diffH >= 0 ? `en ${diffH}h` : `hace ${-diffH}h`;
}

export function DashboardPage() {
  const { data: status, isLoading, isError, error } = useBotStatus();
  const qc = useQueryClient();

  const pauseMutation = useMutation({
    mutationFn: pauseBot,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bot', 'status'] }),
  });
  const resumeMutation = useMutation({
    mutationFn: resumeBot,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bot', 'status'] }),
  });

  if (isLoading) return <Loading label="Consultando estado del bot…" />;
  if (isError) return <ErrorCard error={error} />;

  const isPaused = status.extras.paused;
  const isDryRun = status.extras.dry_run;
  const gateOpen = status.gate.open;
  const nextBroadcast = status.scheduler.jobs[0]?.next_run_time;

  return (
    <div className="anim-fadein">
      {/* ── KPIs ──────────────────────────────────────── */}
      <div className="ds-grid4">
        <KPI
          label="Cola del dispatcher"
          value={status.dispatcher.pending}
          hint={
            status.dispatcher.running ? 'worker corriendo' : '⚠ worker detenido'
          }
          accent={status.dispatcher.pending > 0}
        />
        <KPI
          label="Cuota diaria"
          value={`${status.dispatcher.quota_used_today}/${status.dispatcher.quota_total}`}
          hint="se reinicia a las 00:00 Bogotá"
        />
        <KPI
          label="Ventana operativa"
          value={gateOpen ? 'Abierta' : 'Cerrada'}
          hint={
            gateOpen
              ? `cierra ${formatRelative(status.gate.closes_at)}`
              : `abre ${formatRelative(status.gate.opens_at)}`
          }
          accent={gateOpen}
        />
        <KPI
          label="Broadcasts agendados"
          value={status.scheduler.jobs.length}
          hint={
            nextBroadcast ? `próximo: ${formatDate(nextBroadcast)}` : '—'
          }
        />
      </div>

      {/* ── Estado + acciones rápidas ──────────────────── */}
      <div className="ds-grid2">
        <Card>
          <CardTitle icon={<Activity size={18} />}>Estado general</CardTitle>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Bot</span>
              {isPaused ? (
                <Badge variant="warn">Pausado</Badge>
              ) : (
                <Badge variant="ok">Activo</Badge>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Modo</span>
              {isDryRun ? (
                <Badge variant="warn">Dry-run (no envía)</Badge>
              ) : (
                <Badge variant="ok">Producción</Badge>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Nivel de log</span>
              <Badge variant="neutral">{status.extras.log_level}</Badge>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-300">Whitelist</span>
              <Badge variant="neutral">
                {status.extras.whitelist_count} número
                {status.extras.whitelist_count === 1 ? '' : 's'}
              </Badge>
            </div>
          </div>

          <hr className="ds-hr" />

          <div className="flex gap-2">
            {isPaused ? (
              <Button
                variant="primary"
                icon={<Play size={16} />}
                onClick={() => resumeMutation.mutate()}
                disabled={resumeMutation.isPending}
              >
                {resumeMutation.isPending ? 'Reanudando…' : 'Reanudar bot'}
              </Button>
            ) : (
              <Button
                variant="danger"
                icon={<Pause size={16} />}
                onClick={() => pauseMutation.mutate()}
                disabled={pauseMutation.isPending}
              >
                {pauseMutation.isPending ? 'Pausando…' : 'Pausar bot'}
              </Button>
            )}
          </div>
        </Card>

        <Card>
          <CardTitle icon={<Megaphone size={18} />}>
            Broadcasts agendados
          </CardTitle>

          {status.scheduler.jobs.length === 0 ? (
            <div className="text-sm text-slate-400">
              No hay broadcasts programados. Crea uno desde la sección{' '}
              <span className="text-slate-200">Broadcasts</span>.
            </div>
          ) : (
            <div className="ds-file-list">
              {status.scheduler.jobs.map((j) => (
                <div key={j.id} className="ds-file-row">
                  <div className="flex items-center gap-2">
                    <Clock size={14} className="text-slate-400" />
                    <span className="font-mono text-xs">{j.name}</span>
                  </div>
                  <span className="text-xs text-slate-400">
                    {formatRelative(j.next_run_time)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* ── Sistema (info técnica) ─────────────────────── */}
      <Card>
        <CardTitle icon={<Zap size={18} />} subtitle="Snapshot del backend">
          Detalles del sistema
        </CardTitle>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-slate-400">Dispatcher running</span>
            <span className="font-mono">
              {String(status.dispatcher.running)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">Scheduler running</span>
            <span className="font-mono">
              {String(status.scheduler.running)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">Gate (TZ Bogotá)</span>
            <span className="font-mono">
              {gateOpen ? 'open' : 'closed'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">Reloj del bot</span>
            <span className="font-mono text-xs">{formatDate(status.now)}</span>
          </div>
        </div>
      </Card>
    </div>
  );
}
