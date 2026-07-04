# Alpha Bot Web

Frontend React + Vite que consume la API HTTP del bot (Flask + ngrok en Google Colab).

## Estructura

```
frontend/
├── public/
│   ├── config.js     ← URL del bot (EDITABLE post-build)
│   ├── 404.html      ← SPA fallback para GitHub Pages
│   └── favicon.svg
├── src/
│   ├── api/client.js        ← Axios + resolución de baseURL
│   ├── pages/               ← Dashboard, Argos, Configuración, Broadcasts, Logs
│   ├── components/          ← UI, charts, layout, config forms
│   ├── hooks/
│   └── styles/design-system.css
├── .github/workflows/deploy.yml   ← Auto-deploy a GitHub Pages
├── vite.config.js
└── package.json
```

## Cómo se conecta al bot

El cliente HTTP resuelve la URL del bot en este orden:

1. `window.__BOT_API__` — inyectado por `public/config.js`. **Esta es la vía
   en producción** (GitHub Pages): edita `config.js` directamente sin
   reconstruir.
2. `import.meta.env.VITE_API_URL` — alternativa build-time.
3. Vacío → usa el proxy de Vite hacia `localhost:5000` (modo `npm run dev`).

## Desarrollo local

```bash
npm install
npm run dev
# abre http://localhost:5173
```

Asegúrate de que el bot esté corriendo localmente en `http://localhost:5000`
(o cambia el `target` del proxy en `vite.config.js`).

## Build para producción

```bash
# Para servir desde la raíz de un dominio:
npm run build

# Para servir bajo subpath (GitHub Pages típico: USER.github.io/REPO):
VITE_BASE=/REPO/ npm run build
```

El bundle queda en `dist/`. Si lo subes manualmente a GitHub Pages, copia
TODO el contenido de `dist/` al branch `gh-pages`. Si usas el workflow de
`.github/workflows/deploy.yml`, lo hace solo en cada push a `main`.

## Cambiar la URL del bot tras un reinicio de Colab

Cada vez que arranca el bot, ngrok te da una URL nueva. Para apuntar el
front a la URL nueva **sin** reconstruir:

1. En GitHub: ve a tu repo → branch en que esté publicado el site
   (típicamente `main` si usas el workflow incluido, porque el workflow
   reconstruye en cada push y `config.js` se copia tal cual desde
   `public/config.js`).
2. Edita `public/config.js` (en la branch `main`) — cambia la línea
   `window.__BOT_API__ = '...'` a la URL nueva de ngrok.
3. Commit → push.
4. Espera ~1 min al deploy → recarga el navegador.

Alternativa más rápida si quieres saltarte el rebuild: en Settings → Pages,
configura "Deploy from a branch" → branch `gh-pages` y edita
`config.js` directamente ahí cada vez. Pero el workflow incluido usa la
opción "GitHub Actions" que es más estándar.

## Endpoints que consume

Todos relativos a la URL del bot. Documentados en `src/api/client.js`.

- `GET  /health`
- `GET  /api/bot/status`
- `POST /api/bot/pause`
- `POST /api/bot/resume`
- `GET  /api/bot/logs?level&limit&since`
- `GET  /api/config` | `GET /api/config/{section}` | `PUT /api/config/{section}`
- `GET  /api/broadcasts` | `POST` | `PUT /api/broadcasts/{id}` |
  `DELETE /api/broadcasts/{id}` | `POST /api/broadcasts/{id}/run-now`
- `GET  /api/argos/precios-regionales`
- `GET  /api/argos/intervalos-hdi?cod_municipio`
- `GET  /api/argos/perfiles-alertas`
- `GET  /api/argos/mapa`
- `GET  /api/argos/files`
- `POST /api/argos/refresh?name`
