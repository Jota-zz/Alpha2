/**
 * Cliente de descarga de CSVs publicados (modo MVP).
 *
 * No hay OAuth ni API Key. Cada URL viene de "Publicar en la web → CSV" en
 * Google Sheets, así que es totalmente pública y se baja con un fetch normal.
 *
 * Si más adelante querés volver a archivos privados, este módulo es el lugar
 * para reintroducir el flujo GIS / Drive API (ver historial de git).
 */

import { CSV_URLS } from '../config/drive';

/**
 * Descarga el texto plano de una URL publicada. Si el servidor responde 404
 * (URL mal publicada) o 5xx, propaga el error con info útil para debuggear.
 */
export async function downloadCsv(url) {
  if (!url) throw new Error('URL vacía: revisá las variables VITE_CSV_*_URL.');
  const resp = await fetch(url, { credentials: 'omit' });
  if (!resp.ok) {
    throw new Error(
      `No pude descargar ${url} → ${resp.status} ${resp.statusText}. ` +
        `Verificá que la hoja esté "Publicada en la web" y que la URL termine en ?output=csv.`
    );
  }
  return resp.text();
}

/**
 * Descarga los 3 CSVs Argos en paralelo. Devuelve { precios, intervalos,
 * perfiles } con el texto crudo de cada uno. Si alguna URL falla, propaga
 * el error (Promise.all corta en el primer reject).
 */
export async function downloadArgosCsvs() {
  const entries = await Promise.all(
    Object.entries(CSV_URLS).map(async ([key, url]) => {
      const text = await downloadCsv(url);
      return [key, { text }];
    })
  );
  return Object.fromEntries(entries);
}
