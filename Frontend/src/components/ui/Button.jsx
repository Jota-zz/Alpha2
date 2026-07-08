/**
 * Botones de la app. Mapean a las clases .ds-btn-*:
 *   - primary: accent verde lima sólido (acciones principales)
 *   - ghost:   transparente con borde sutil (acciones secundarias)
 *   - danger:  rojo translúcido (delete / pause)
 *
 * Uso:
 *   <Button variant="primary" icon={<Save />} onClick={save}>Guardar</Button>
 */

const VARIANT_CLASS = {
  primary: 'ds-btn-primary',
  ghost: 'ds-btn-ghost',
  danger: 'ds-btn-danger',
};

export function Button({
  variant = 'primary',
  icon,
  children,
  className = '',
  ...rest
}) {
  const cls = VARIANT_CLASS[variant] || VARIANT_CLASS.primary;
  return (
    <button className={`${cls} ${className}`} {...rest}>
      {icon}
      <span>{children}</span>
    </button>
  );
}
