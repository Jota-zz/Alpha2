/**
 * Hook stub para mantener compatibilidad con la versión OAuth anterior.
 *
 * En modo MVP no hay sesión: los CSVs son URLs públicas. Este hook reporta
 * únicamente si las URLs están configuradas o no, para que la página Argos
 * pueda decidir si mostrar el banner de "config faltante" o pasar a cargar.
 */

import { areCsvUrlsConfigured } from '../config/drive';

export function useDrive() {
  const configured = areCsvUrlsConfigured();
  return {
    status: configured ? 'connected' : 'idle',
    configured,
  };
}
