/**
 * Página Broadcasts: CRUD de mensajes programados.
 *
 * Layout:
 *   - Header con botón "Nuevo broadcast"
 *   - Lista de broadcasts con: día/hora, topic, último ejecución, acciones
 *     (run-now, editar, borrar, toggle enabled).
 *   - Modal compartido para crear/editar.
 *
 * Cada operación invalida la query 'bot.status' además de 'broadcasts'
 * porque el TopBar muestra el contador de jobs registrados en el scheduler.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Megaphone,
  Plus,
  Play,
  Pencil,
  Trash2,
  Calendar,
} from 'lucide-react';
import {
  createBroadcast,
  deleteBroadcast,
  fetchBroadcasts,
  runBroadcastNow,
  updateBroadcast,
} from '../api/client';
import { Card, CardTitle } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { Modal } from '../components/ui/Modal';
import { Toggle } from '../components/ui/Toggle';
import { ErrorCard, EmptyState, Loading } from '../components/ui/EmptyState';
import { toast } from '../components/ui/ToastHost';

const DAY_OPTIONS = [
  { value: 'mon', label: 'Lunes' },
  { value: 'tue', label: 'Martes' },
  { value: 'wed', label: 'Miércoles' },
  { value: 'thu', label: 'Jueves' },
  { value: 'fri', label: 'Viernes' },
  { value: 'sat', label: 'Sábado' },
  { value: 'sun', label: 'Domingo' },
];

const DAY_LABEL_FROM_VALUE = Object.fromEntries(
  DAY_OPTIONS.map((d) => [d.value, d.label])
);

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

// ────────────────────────────────────────────────────────────────────
// Modal de crear/editar
// ────────────────────────────────────────────────────────────────────
function BroadcastModal({ open, onClose, editing }) {
  const qc = useQueryClient();
  const isEdit = !!editing;

  const [form, setForm] = useState(
    editing || {
      topic: '',
      day_of_week: 'fri',
      hour: 10,
      minute: 0,
      enabled: true,
    }
  );

  // Reset al abrir
  useState(() => setForm(editing || form));

  const m = useMutation({
    mutationFn: (body) =>
      isEdit ? updateBroadcast(editing.id, body) : createBroadcast(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['broadcasts'] });
      qc.invalidateQueries({ queryKey: ['bot', 'status'] });
      toast.success(isEdit ? 'Broadcast actualizado' : 'Broadcast creado');
      onClose();
    },
    onError: (err) => {
      const detail = err?.response?.data?.detail;
      const msg =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
          ? detail.map((d) => d.msg).join(' · ')
          : 'No se pudo guardar';
      toast.error(msg);
    },
  });

  const canSubmit = form.topic && form.topic.length >= 3 && !m.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? 'Editar broadcast' : 'Nuevo broadcast'}
    >
      <div className="ds-config-field">
        <label>Topic</label>
        <textarea
          value={form.topic}
          placeholder="ej. Buenos días, ¿cómo están los precios de cemento esta semana?"
          onChange={(e) => setForm({ ...form, topic: e.target.value })}
        />
        <div className="ds-hint">
          El topic se usa como contexto para que la IA genere el mensaje
          outreach. Mínimo 3 caracteres.
        </div>
      </div>

      <div className="ds-grid3" style={{ marginBottom: '1.5rem' }}>
        <div className="ds-config-field" style={{ marginBottom: 0 }}>
          <label>Día</label>
          <select
            value={form.day_of_week}
            onChange={(e) => setForm({ ...form, day_of_week: e.target.value })}
          >
            {DAY_OPTIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </div>

        <div className="ds-config-field" style={{ marginBottom: 0 }}>
          <label>Hora (Bogotá)</label>
          <select
            value={form.hour}
            onChange={(e) => setForm({ ...form, hour: Number(e.target.value) })}
          >
            {Array.from({ length: 24 }, (_, i) => (
              <option key={i} value={i}>
                {i.toString().padStart(2, '0')}
              </option>
            ))}
          </select>
        </div>

        <div className="ds-config-field" style={{ marginBottom: 0 }}>
          <label>Minuto</label>
          <select
            value={form.minute}
            onChange={(e) =>
              setForm({ ...form, minute: Number(e.target.value) })
            }
          >
            {[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].map((m) => (
              <option key={m} value={m}>
                {m.toString().padStart(2, '0')}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex items-center justify-between mb-4">
        <span className="text-sm text-slate-200">Habilitado</span>
        <Toggle
          checked={form.enabled}
          onChange={(v) => setForm({ ...form, enabled: v })}
        />
      </div>

      <hr className="ds-hr" />

      <div className="flex gap-2 justify-end">
        <Button variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
        <Button
          variant="primary"
          onClick={() => m.mutate(form)}
          disabled={!canSubmit}
        >
          {m.isPending ? 'Guardando…' : isEdit ? 'Guardar' : 'Crear'}
        </Button>
      </div>
    </Modal>
  );
}

// ────────────────────────────────────────────────────────────────────
// Página
// ────────────────────────────────────────────────────────────────────
export function BroadcastsPage() {
  const qc = useQueryClient();
  const [modal, setModal] = useState({ open: false, editing: null });

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['broadcasts'],
    queryFn: fetchBroadcasts,
    staleTime: 20_000,
  });

  const deleteMutation = useMutation({
    mutationFn: deleteBroadcast,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['broadcasts'] });
      qc.invalidateQueries({ queryKey: ['bot', 'status'] });
      toast.success('Broadcast borrado');
    },
    onError: () => toast.error('No se pudo borrar'),
  });

  const runNowMutation = useMutation({
    mutationFn: runBroadcastNow,
    onSuccess: (result) => {
      if (result.ok === false) {
        toast.error(`No se ejecutó: ${result.reason}`);
      } else {
        toast.success(
          `Disparado: ${result.enviadas ?? 0} envíos, ${result.falladas ?? 0} fallidos`
        );
      }
      qc.invalidateQueries({ queryKey: ['broadcasts'] });
    },
    onError: () => toast.error('Error al ejecutar'),
  });

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }) => updateBroadcast(id, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['broadcasts'] });
      qc.invalidateQueries({ queryKey: ['bot', 'status'] });
    },
    onError: () => toast.error('No se pudo actualizar'),
  });

  if (isLoading) return <Loading label="Cargando broadcasts…" />;
  if (isError) return <ErrorCard error={error} />;

  return (
    <div className="anim-fadein">
      <div className="flex items-center justify-between mb-6">
        <p className="text-sm text-slate-400">
          Mensajes programados que envían plantillas outreach a todas las
          ferreterías con estado <span className="font-mono text-slate-300">NULL</span>.
        </p>
        <Button
          variant="primary"
          icon={<Plus size={16} />}
          onClick={() => setModal({ open: true, editing: null })}
        >
          Nuevo broadcast
        </Button>
      </div>

      <Card>
        <CardTitle icon={<Megaphone size={18} />}>
          {data.length} broadcast{data.length === 1 ? '' : 's'}
        </CardTitle>

        {data.length === 0 ? (
          <EmptyState
            icon="📢"
            title="Aún no hay broadcasts programados"
          >
            Crea uno con el botón superior derecho.
          </EmptyState>
        ) : (
          <div className="ds-file-list">
            {data.map((b) => (
              <div key={b.id} className="ds-file-row">
                <div className="flex items-center gap-4 min-w-0 flex-1">
                  <Calendar
                    size={16}
                    className={b.enabled ? 'text-slate-300' : 'text-slate-600'}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-xs text-slate-200">
                        {DAY_LABEL_FROM_VALUE[b.day_of_week]}{' '}
                        {b.hour.toString().padStart(2, '0')}:
                        {b.minute.toString().padStart(2, '0')}
                      </span>
                      {b.enabled ? (
                        <Badge variant="ok">activo</Badge>
                      ) : (
                        <Badge variant="neutral">deshabilitado</Badge>
                      )}
                    </div>
                    <div
                      className="text-xs text-slate-400 truncate"
                      title={b.topic}
                    >
                      {b.topic}
                    </div>
                    {b.last_run_at && (
                      <div className="text-[0.65rem] text-slate-500 mt-1">
                        última ejecución: {formatDate(b.last_run_at)}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <Toggle
                    checked={b.enabled}
                    onChange={(v) =>
                      toggleEnabled.mutate({ id: b.id, enabled: v })
                    }
                  />
                  <button
                    className="p-2 rounded-lg hover:bg-white/10 text-slate-400 hover:text-slate-200 transition"
                    onClick={() => runNowMutation.mutate(b.id)}
                    disabled={runNowMutation.isPending || !b.enabled}
                    title="Ejecutar ahora"
                  >
                    <Play size={14} />
                  </button>
                  <button
                    className="p-2 rounded-lg hover:bg-white/10 text-slate-400 hover:text-slate-200 transition"
                    onClick={() => setModal({ open: true, editing: b })}
                    title="Editar"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    className="p-2 rounded-lg hover:bg-red-500/10 text-slate-400 hover:text-red-300 transition"
                    onClick={() => {
                      if (
                        confirm(
                          `¿Borrar el broadcast del ${DAY_LABEL_FROM_VALUE[b.day_of_week]} ${b.hour.toString().padStart(2, '0')}:${b.minute.toString().padStart(2, '0')}?`
                        )
                      ) {
                        deleteMutation.mutate(b.id);
                      }
                    }}
                    title="Borrar"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <BroadcastModal
        key={modal.editing?.id || 'new'}
        open={modal.open}
        onClose={() => setModal({ open: false, editing: null })}
        editing={modal.editing}
      />
    </div>
  );
}
