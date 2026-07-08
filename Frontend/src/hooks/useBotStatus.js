/**
 * Hook que pollea /api/bot/status cada 5 segundos.
 *
 * Refresca en background incluso cuando la pestaña no tiene foco (porque
 * el bot puede pausarse mientras el operador está en otra pestaña y
 * queremos que el badge cambie cuando vuelva).
 */

import { useQuery } from '@tanstack/react-query';
import { fetchBotStatus } from '../api/client';

export function useBotStatus() {
  return useQuery({
    queryKey: ['bot', 'status'],
    queryFn: fetchBotStatus,
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
    staleTime: 4_000,
    // No reintentar agresivo si el backend está caído
    retry: 1,
  });
}
