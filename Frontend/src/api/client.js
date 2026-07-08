/**
 * Cliente HTTP único hacia el backend del bot (Flask + ngrok en Colab).
 *
 * Resolución de baseURL en este orden:
 *   1. `window.__BOT_API__` → inyectado por /config.js en runtime.
 *      Esto es lo que se usa en GitHub Pages: cada vez que reinicies el
 *      bot en Colab te dan una URL nueva de ngrok, y solo editas
 *      `public/config.js` (o el `config.js` ya desplegado en gh-pages)
 *      sin reconstruir el bundle.
 *   2. `import.meta.env.VITE_API_URL` → si lo defines al hacer `npm run build`.
 *   3. `''` (string vacío) → usa el proxy de Vite en desarrollo
 *      (vite.config.js redirige /api, /webhook, /health a localhost:8000).
 */

import axios from 'axios';

function resolveBaseURL() {
  // 1. Runtime config inyectado vía window.__BOT_API__ (config.js)
  if (typeof window !== 'undefined' && window.__BOT_API__) {
    let url = String(window.__BOT_API__).trim();
    if (!url) return '';
    if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
    return url.replace(/\/$/, '');
  }
  // 2. Build-time env var
  const envUrl = import.meta.env.VITE_API_URL;
  if (envUrl) {
    let url = String(envUrl).trim();
    if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
    return url.replace(/\/$/, '');
  }
  // 3. Dev: proxy de Vite
  return '';
}

const baseURL = resolveBaseURL();

if (typeof window !== 'undefined' && !window.__bot_api_logged) {
  // eslint-disable-next-line no-console
  console.log('[api] baseURL =', baseURL || '(dev / vite proxy)');
  window.__bot_api_logged = true;
}

export const api = axios.create({
  baseURL,
  timeout: 30_000,
  headers: {
    'Content-Type': 'application/json',
    // ngrok mete una página interstitial si no mandas este header.
    'ngrok-skip-browser-warning': 'true',
  },
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      console.error(
        `[api] ${error.config?.method?.toUpperCase()} ${error.config?.url} → ${
          error.response.status
        }`,
        error.response.data
      );
    } else if (error.request) {
      console.error(
        `[api] ${error.config?.method?.toUpperCase()} ${error.config?.url} → sin respuesta (CORS / bot apagado / URL incorrecta)`
      );
    } else {
      console.error('[api] error desconocido:', error.message);
    }
    return Promise.reject(error);
  }
);

// ── Health & status ───────────────────────────────────────────────
export const fetchHealth = () => api.get('/health').then((r) => r.data);
export const fetchBotStatus = () =>
  api.get('/api/bot/status').then((r) => r.data);

// ── Bot control ───────────────────────────────────────────────────
export const pauseBot = () => api.post('/api/bot/pause').then((r) => r.data);
export const resumeBot = () => api.post('/api/bot/resume').then((r) => r.data);
export const fetchLogs = ({ level, limit = 200, since } = {}) =>
  api
    .get('/api/bot/logs', { params: { level, limit, since } })
    .then((r) => r.data);

// ── Configuración (5 secciones) ───────────────────────────────────
export const fetchAllConfig = () =>
  api.get('/api/config').then((r) => r.data);

export const fetchConfig = (section) =>
  api.get(`/api/config/${section}`).then((r) => r.data);

export const updateConfig = (section, body) =>
  api.put(`/api/config/${section}`, body).then((r) => r.data);

// ── Broadcasts ────────────────────────────────────────────────────
export const fetchBroadcasts = () =>
  api.get('/api/broadcasts').then((r) => r.data);

export const createBroadcast = (body) =>
  api.post('/api/broadcasts', body).then((r) => r.data);

export const updateBroadcast = (id, body) =>
  api.put(`/api/broadcasts/${id}`, body).then((r) => r.data);

export const deleteBroadcast = (id) =>
  api.delete(`/api/broadcasts/${id}`).then((r) => r.data);

export const runBroadcastNow = (id) =>
  api.post(`/api/broadcasts/${id}/run-now`).then((r) => r.data);

// ── Argos ─────────────────────────────────────────────────────────
export const fetchArgosPreciosRegionales = () =>
  api.get('/api/argos/precios-regionales').then((r) => r.data);

export const fetchArgosIntervalosHdi = (codMunicipio) =>
  api
    .get('/api/argos/intervalos-hdi', {
      params: codMunicipio ? { cod_municipio: codMunicipio } : {},
    })
    .then((r) => r.data);

export const fetchArgosPerfilesAlertas = () =>
  api.get('/api/argos/perfiles-alertas').then((r) => r.data);

export const fetchArgosMapa = () => api.get('/api/argos/mapa').then((r) => r.data);

export const fetchArgosFiles = () => api.get('/api/argos/files').then((r) => r.data);

export const refreshArgosCache = (name) =>
  api
    .post('/api/argos/refresh', null, { params: name ? { name } : {} })
    .then((r) => r.data);
