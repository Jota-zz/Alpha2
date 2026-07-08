/**
 * Descarga los 3 CSVs publicados de Google Sheets y los expone vía
 * react-query. Cache de 5 min; el botón "Refrescar" en la página invalida
 * la query y fuerza re-fetch.
 *
 * Las URLs vienen de `src/config/drive.js` (variables VITE_CSV_*_URL).
 */

import { useQuery } from '@tanstack/react-query';
import { CSV_URLS, areCsvUrlsConfigured } from '../config/drive';
import { downloadArgosCsvs } from '../lib/gdrive';

export function useArgosCsvs() {
  return useQuery({
    queryKey: ['argos-csvs', CSV_URLS.precios, CSV_URLS.intervalos, CSV_URLS.perfiles],
    enabled: areCsvUrlsConfigured(),
    queryFn: () => downloadArgosCsvs(),
    staleTime: 5 * 60_000,
    retry: 1,
  });
}
