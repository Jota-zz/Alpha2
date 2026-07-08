/**
 * Badges de estado pequeños. Mapean a .ds-badge-*:
 *   - ok:      verde       (37,177,119)
 *   - warn:    naranja     (245,159,10)
 *   - crit:    rojo        (239,67,67)
 *   - neutral: gris translúcido
 *
 * Uso:
 *   <Badge variant="ok">Activo</Badge>
 */

const VARIANT_CLASS = {
  ok: 'ds-badge-ok',
  warn: 'ds-badge-warn',
  crit: 'ds-badge-crit',
  neutral: 'ds-badge-neutral',
};

export function Badge({ variant = 'neutral', children, icon }) {
  return (
    <span className={VARIANT_CLASS[variant] || VARIANT_CLASS.neutral}>
      {icon}
      {children}
    </span>
  );
}
