/**
 * Modal genérico para crear/editar broadcasts.
 *
 * Uso:
 *   <Modal open={isOpen} onClose={close} title="Nuevo broadcast">
 *     <form>...</form>
 *   </Modal>
 *
 * Cierra con click en el backdrop o con la X.
 */

import { useEffect } from 'react';
import { X } from 'lucide-react';

export function Modal({ open, onClose, title, children }) {
  // Cerrar con Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Bloquear scroll del body cuando el modal está abierto
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center anim-fadein"
      style={{
        background: 'rgba(2, 6, 23, 0.7)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={onClose}
    >
      <div
        className="ds-card scrollbar-thin"
        style={{
          maxWidth: 520,
          width: 'calc(100% - 32px)',
          maxHeight: 'calc(100vh - 64px)',
          overflowY: 'auto',
          margin: 0,
          padding: '1.5rem',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-slate-200 transition"
            aria-label="Cerrar"
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
