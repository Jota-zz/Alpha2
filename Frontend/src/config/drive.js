/**
 * Config de las fuentes de datos del Dashboard (Argos).
 *
 * MODO MVP: cada CSV vive como un Google Sheet publicado a la web. La URL
 * pública del export CSV se consume directo con `fetch()` — no requiere
 * OAuth, ni API Key, ni Service Account. Esa simplicidad es la razón de
 * usar este modo en el MVP.
 *
 * Cómo obtener la URL para cada hoja:
 *   1. Subí el CSV a Google Drive y abrilo como Google Sheet.
 *   2. Archivo → Compartir → Publicar en la web.
 *   3. Elegí la pestaña/hoja correcta y formato "Valores separados por comas (.csv)".
 *   4. Apretá "Publicar". Te devuelve una URL del tipo:
 *        https://docs.google.com/spreadsheets/d/e/2PACX-XXX/pub?output=csv
 *   5. Pegá esa URL en `.env.local` con la variable correspondiente.
 *
 * Alternativa: si preferís dejar el archivo como Sheet privado pero seguir
 * usándolo, también funciona la URL "export":
 *      https://docs.google.com/spreadsheets/d/SHEET_ID/export?format=csv&gid=0
 * con la hoja compartida como "cualquiera con el enlace puede ver".
 */

// URLs publicadas a la web de cada CSV. Vienen de Vite env vars (build-time).
export const CSV_URLS = {
  precios: import.meta.env.VITE_CSV_PRECIOS_URL || '',
  intervalos: import.meta.env.VITE_CSV_INTERVALOS_URL || '',
  perfiles: import.meta.env.VITE_CSV_PERFILES_URL || '',
};

/** ¿Están configuradas las 3 URLs? */
export function areCsvUrlsConfigured() {
  return Boolean(CSV_URLS.precios && CSV_URLS.intervalos && CSV_URLS.perfiles);
}

/** Lista de URLs faltantes, para mostrar al usuario qué le falta configurar. */
export function missingCsvUrls() {
  const missing = [];
  if (!CSV_URLS.precios) missing.push('VITE_CSV_PRECIOS_URL');
  if (!CSV_URLS.intervalos) missing.push('VITE_CSV_INTERVALOS_URL');
  if (!CSV_URLS.perfiles) missing.push('VITE_CSV_PERFILES_URL');
  return missing;
}
