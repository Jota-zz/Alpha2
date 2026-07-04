/**
 * Toast system minimalista, sin dependencias externas.
 *
 * Uso:
 *   import { toast } from '../hooks/useToast';
 *   toast.success('Guardado');
 *   toast.error('Error al guardar');
 *
 * Hay que montar <ToastHost /> una sola vez en App.jsx.
 */

import { useEffect, useState } from 'react';

let _id = 0;
const _listeners = new Set();
const _state = { items: [] };

function _emit() {
  _listeners.forEach((fn) => fn([..._state.items]));
}

function _push(variant, message, ttl = 3000) {
  const id = ++_id;
  _state.items.push({ id, variant, message });
  _emit();
  setTimeout(() => {
    _state.items = _state.items.filter((t) => t.id !== id);
    _emit();
  }, ttl);
}

export const toast = {
  success: (msg) => _push('success', msg),
  error: (msg) => _push('error', msg, 5000),
  info: (msg) => _push('info', msg),
};

export function ToastHost() {
  const [items, setItems] = useState([]);

  useEffect(() => {
    const fn = (next) => setItems(next);
    _listeners.add(fn);
    return () => _listeners.delete(fn);
  }, []);

  if (!items.length) return null;

  return (
    <div
      className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none"
      style={{ maxWidth: 360 }}
    >
      {items.map((t) => (
        <div
          key={t.id}
          className={`anim-fadein pointer-events-auto px-4 py-3 rounded-2xl text-sm font-medium border ${
            t.variant === 'success'
              ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-100'
              : t.variant === 'error'
              ? 'bg-red-500/15 border-red-500/40 text-red-100'
              : 'bg-white/[0.06] border-white/15 text-slate-100'
          }`}
          style={{ backdropFilter: 'blur(6px)' }}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
