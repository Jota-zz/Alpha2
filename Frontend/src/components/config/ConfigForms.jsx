/**
 * Formularios de configuración (5 secciones).
 *
 * Patrón compartido por cada form:
 *   1. Recibe `initial` desde GET /api/config/{section}.
 *   2. Mantiene estado local con useState; submit hace PUT y muestra toast.
 *   3. Botón "Guardar" deshabilitado mientras no haya cambios o esté en vuelo.
 *   4. Validación min/max básica en cliente (la validación dura la hace el
 *      backend con los schemas Pydantic).
 *
 * NOTAS:
 *   - Los `outreach_template_*` aparecen DUPLICADOS entre AnthropicConfig y
 *     WebhookConfig en el backend (por motivos explicados ahí). Aquí los
 *     mostramos solo en la pestaña Webhook para no confundir.
 */

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Save } from 'lucide-react';
import { updateConfig } from '../../api/client';
import { Button } from '../ui/Button';
import { Toggle } from '../ui/Toggle';
import { toast } from '../ui/ToastHost';

// ───────────────────────────────────────────────────────────────────
// Hook compartido: mutation + estado dirty
// ───────────────────────────────────────────────────────────────────
function useSectionMutation(section, initial) {
  const qc = useQueryClient();
  const [value, setValue] = useState(initial);
  // Reset cuando llega un nuevo `initial` (cambio de pestaña/refetch)
  useEffect(() => {
    setValue(initial);
  }, [initial]);

  const m = useMutation({
    mutationFn: (body) => updateConfig(section, body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: ['bot', 'status'] });
      setValue(data);
      toast.success('Guardado');
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

  const dirty = JSON.stringify(value) !== JSON.stringify(initial);
  return { value, setValue, m, dirty };
}

// Helper compacto para campos numéricos con label + hint
function NumberField({ label, value, onChange, min, max, step = 1, hint }) {
  return (
    <div className="ds-config-field">
      <label>{label}</label>
      <input
        type="number"
        value={value ?? ''}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const v = e.target.value === '' ? '' : Number(e.target.value);
          onChange(v);
        }}
      />
      {hint ? <div className="ds-hint">{hint}</div> : null}
    </div>
  );
}

function TextField({ label, value, onChange, hint, type = 'text', placeholder }) {
  return (
    <div className="ds-config-field">
      <label>{label}</label>
      <input
        type={type}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint ? <div className="ds-hint">{hint}</div> : null}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// 1. Dispatcher
// ───────────────────────────────────────────────────────────────────
export function DispatcherForm({ initial }) {
  const { value, setValue, m, dirty } = useSectionMutation('dispatcher', initial);

  return (
    <div>
      <p className="text-sm text-slate-400 mb-4">
        Delays human-like del worker async. Cambios surten efecto en el siguiente
        encolado; items ya en la cola conservan su delay original.
      </p>

      <div className="ds-grid2">
        <div>
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
            Ventana de escucha (inbound)
          </h3>
          <NumberField
            label="Mínimo (s)"
            value={value.listen_window_min_s}
            onChange={(v) => setValue({ ...value, listen_window_min_s: v })}
            min={0}
            max={3600}
            hint="Tiempo de espera tras un inbound antes de generar respuesta. Acumula mensajes consecutivos del mismo número."
          />
          <NumberField
            label="Máximo (s)"
            value={value.listen_window_max_s}
            onChange={(v) => setValue({ ...value, listen_window_max_s: v })}
            min={0}
            max={3600}
          />
        </div>

        <div>
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
            Delay outreach (broadcast)
          </h3>
          <NumberField
            label="Mínimo (s)"
            value={value.outreach_delay_min_s}
            onChange={(v) => setValue({ ...value, outreach_delay_min_s: v })}
            min={0}
            max={3600}
            hint="Delay aleatorio antes del primer envío proactivo a cada ferretería."
          />
          <NumberField
            label="Máximo (s)"
            value={value.outreach_delay_max_s}
            onChange={(v) => setValue({ ...value, outreach_delay_max_s: v })}
            min={0}
            max={3600}
          />
        </div>
      </div>

      <div>
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Inter-chat (entre números distintos)
        </h3>
        <div className="ds-grid2" style={{ marginBottom: 0 }}>
          <NumberField
            label="Mínimo (s)"
            value={value.inter_chat_min_s}
            onChange={(v) => setValue({ ...value, inter_chat_min_s: v })}
            min={0}
            max={3600}
          />
          <NumberField
            label="Máximo (s)"
            value={value.inter_chat_max_s}
            onChange={(v) => setValue({ ...value, inter_chat_max_s: v })}
            min={0}
            max={3600}
          />
        </div>
      </div>

      <hr className="ds-hr" />

      <Button
        variant="primary"
        icon={<Save size={16} />}
        onClick={() => m.mutate(value)}
        disabled={!dirty || m.isPending}
      >
        {m.isPending ? 'Guardando…' : 'Guardar cambios'}
      </Button>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// 2. Anthropic
// ───────────────────────────────────────────────────────────────────
export function AnthropicForm({ initial }) {
  const { value, setValue, m, dirty } = useSectionMutation('anthropic', initial);

  return (
    <div>
      <p className="text-sm text-slate-400 mb-4">
        Configuración del modelo de Anthropic. El siguiente turno conversacional
        ya usa los valores nuevos.
      </p>

      <TextField
        label="Modelo"
        value={value.model_name}
        onChange={(v) => setValue({ ...value, model_name: v })}
        hint="ID del modelo Anthropic (ej. claude-haiku-4-5-20251001)."
      />

      <div className="ds-grid2" style={{ marginBottom: 0 }}>
        <NumberField
          label="max_tokens"
          value={value.max_tokens}
          onChange={(v) => setValue({ ...value, max_tokens: v })}
          min={1}
          max={8192}
          hint="Tope de tokens por respuesta."
        />
        <NumberField
          label="temperature"
          value={value.temperature}
          onChange={(v) => setValue({ ...value, temperature: v })}
          min={0}
          max={1}
          step={0.1}
          hint="0 = determinístico, 1 = creativo."
        />
        <NumberField
          label="typo_rate"
          value={value.typo_rate}
          onChange={(v) => setValue({ ...value, typo_rate: v })}
          min={0}
          max={0.05}
          step={0.001}
          hint="Probabilidad de typo human-like por letra (0.005 = 1 cada 200 letras)."
        />
        <NumberField
          label="history_limit (turnos)"
          value={value.history_limit}
          onChange={(v) => setValue({ ...value, history_limit: v })}
          min={0}
          max={200}
          hint="Cuántos turnos previos enviar al modelo."
        />
      </div>

      <hr className="ds-hr" />

      <Button
        variant="primary"
        icon={<Save size={16} />}
        onClick={() => m.mutate(value)}
        disabled={!dirty || m.isPending}
      >
        {m.isPending ? 'Guardando…' : 'Guardar cambios'}
      </Button>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// 3. Operating Hours
// ───────────────────────────────────────────────────────────────────
const DAY_LABELS = {
  '0': 'Lunes',
  '1': 'Martes',
  '2': 'Miércoles',
  '3': 'Jueves',
  '4': 'Viernes',
  '5': 'Sábado',
  '6': 'Domingo',
};

export function OperatingHoursForm({ initial }) {
  const { value, setValue, m, dirty } = useSectionMutation('operating_hours', initial);

  // Asegurar que windows tenga las 7 claves
  const windows = { ...value?.windows };
  for (const k of Object.keys(DAY_LABELS)) {
    if (!(k in windows)) windows[k] = [0, 24];
  }

  const setDay = (day, window) => {
    setValue({ ...value, windows: { ...windows, [day]: window } });
  };

  return (
    <div>
      <p className="text-sm text-slate-400 mb-4">
        Ventanas horarias en hora local de Bogotá. Fuera de ventana, los webhooks
        se descartan y los broadcasts esperan al siguiente turno.
      </p>

      <div className="space-y-3">
        {Object.entries(DAY_LABELS).map(([key, label]) => {
          const w = windows[key];
          const isOpen = w !== null;
          const [opens, closes] = w || [8, 18];
          return (
            <div
              key={key}
              className="flex items-center gap-4 py-2 px-3 rounded-xl bg-white/[0.03] border border-white/[0.08]"
            >
              <div className="w-24 text-sm text-slate-200 font-medium">
                {label}
              </div>
              <Toggle
                checked={isOpen}
                onChange={(on) => setDay(key, on ? [8, 18] : null)}
                label={isOpen ? 'Abierto' : 'Cerrado'}
              />
              {isOpen && (
                <div className="flex items-center gap-2 ml-auto">
                  <select
                    className="ds-select"
                    value={opens}
                    onChange={(e) => setDay(key, [Number(e.target.value), closes])}
                  >
                    {Array.from({ length: 24 }, (_, i) => (
                      <option key={i} value={i}>
                        {i.toString().padStart(2, '0')}:00
                      </option>
                    ))}
                  </select>
                  <span className="text-slate-500 text-sm">→</span>
                  <select
                    className="ds-select"
                    value={closes}
                    onChange={(e) => setDay(key, [opens, Number(e.target.value)])}
                  >
                    {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => (
                      <option key={h} value={h}>
                        {h.toString().padStart(2, '0')}:00
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <hr className="ds-hr" />

      <Button
        variant="primary"
        icon={<Save size={16} />}
        onClick={() => m.mutate(value)}
        disabled={!dirty || m.isPending}
      >
        {m.isPending ? 'Guardando…' : 'Guardar cambios'}
      </Button>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// 4. Webhook (plantilla outreach)
// ───────────────────────────────────────────────────────────────────
export function WebhookForm({ initial }) {
  const { value, setValue, m, dirty } = useSectionMutation('webhook', initial);

  return (
    <div>
      <p className="text-sm text-slate-400 mb-4">
        Plantilla aprobada de WhatsApp para abrir conversación con números nuevos
        (fuera de la ventana de 24h tras la última respuesta del cliente).
      </p>

      <TextField
        label="Nombre de la plantilla"
        value={value.outreach_template_name}
        onChange={(v) => setValue({ ...value, outreach_template_name: v })}
        hint="Debe existir y estar aprobada en Meta Business Manager."
        placeholder="saludo"
      />

      <TextField
        label="Idioma"
        value={value.outreach_template_lang}
        onChange={(v) => setValue({ ...value, outreach_template_lang: v })}
        hint="Código de idioma del template (ej. es_CO, es, en)."
        placeholder="es_CO"
      />

      <hr className="ds-hr" />

      <Button
        variant="primary"
        icon={<Save size={16} />}
        onClick={() => m.mutate(value)}
        disabled={!dirty || m.isPending}
      >
        {m.isPending ? 'Guardando…' : 'Guardar cambios'}
      </Button>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// 5. Extras (5 opciones extra del plan)
// ───────────────────────────────────────────────────────────────────
export function ExtrasForm({ initial }) {
  const { value, setValue, m, dirty } = useSectionMutation('extras', initial);

  return (
    <div>
      <p className="text-sm text-slate-400 mb-4">
        Opciones operativas adicionales. <strong>paused</strong> y{' '}
        <strong>log_level</strong> tienen efecto inmediato.
      </p>

      <div className="space-y-4">
        <div className="flex items-center justify-between py-2">
          <div>
            <div className="text-sm font-medium text-slate-200">
              Pausar bot
            </div>
            <div className="ds-hint">
              Detiene envíos físicos. Webhooks siguen llegando y persistiendo
              en BD; al reanudar, el dispatcher procesa la cola pendiente.
            </div>
          </div>
          <Toggle
            checked={value.paused}
            onChange={(v) => setValue({ ...value, paused: v })}
          />
        </div>

        <div className="flex items-center justify-between py-2">
          <div>
            <div className="text-sm font-medium text-slate-200">
              Modo dry-run
            </div>
            <div className="ds-hint">
              Genera respuestas con IA y aplica todo el flujo, pero NO llama a la
              API de WhatsApp. Útil para validar cambios sin gastar saldo.
            </div>
          </div>
          <Toggle
            checked={value.dry_run}
            onChange={(v) => setValue({ ...value, dry_run: v })}
          />
        </div>

        <div className="ds-config-field">
          <label>Whitelist de números (uno por línea)</label>
          <textarea
            value={(value.whitelist || []).join('\n')}
            placeholder="573001234567&#10;573009876543"
            onChange={(e) =>
              setValue({
                ...value,
                whitelist: e.target.value
                  .split('\n')
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
          />
          <div className="ds-hint">
            Si tiene entradas, SOLO esos números reciben mensajes (el resto se
            descarta silenciosamente). Vacío = todos pueden recibir.
          </div>
        </div>

        <NumberField
          label="Cuota diaria de envíos físicos"
          value={value.daily_quota}
          onChange={(v) => setValue({ ...value, daily_quota: v })}
          min={0}
          max={100000}
          hint="Tope total de mensajes a WhatsApp por día. Se reinicia a 00:00 hora Bogotá."
        />

        <div className="ds-config-field">
          <label>Nivel de log</label>
          <select
            value={value.log_level}
            onChange={(e) =>
              setValue({ ...value, log_level: e.target.value })
            }
          >
            <option value="DEBUG">DEBUG (todo)</option>
            <option value="INFO">INFO (operación normal)</option>
            <option value="WARNING">WARNING (solo problemas)</option>
            <option value="ERROR">ERROR (solo crashes)</option>
          </select>
          <div className="ds-hint">
            Cambio en caliente: ajusta el logger raíz sin reiniciar el web service.
          </div>
        </div>
      </div>

      <hr className="ds-hr" />

      <Button
        variant="primary"
        icon={<Save size={16} />}
        onClick={() => m.mutate(value)}
        disabled={!dirty || m.isPending}
      >
        {m.isPending ? 'Guardando…' : 'Guardar cambios'}
      </Button>
    </div>
  );
}
