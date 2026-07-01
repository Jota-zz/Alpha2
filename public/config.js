/**
 * Runtime config del frontend Alpha Bot Platform.
 *
 * ─────────────────────────────────────────────────────────────────────
 *   EDITA SOLO LA LÍNEA `window.__BOT_API__ = '...'` MÁS ABAJO.
 * ─────────────────────────────────────────────────────────────────────
 *
 * ¿Por qué este archivo?
 *   El bot corre en Google Colab + ngrok. Cada vez que reinicias el bot
 *   (cada ~2h, o tras cerrar Colab) ngrok te da una URL pública NUEVA.
 *   Para no tener que reconstruir y redeployar el frontend cada vez,
 *   este archivo se carga ANTES de la app (ver index.html) y solo cambia
 *   la dirección a la que el front hace las llamadas /api/*.
 *
 * ¿Dónde poner la URL?
 *   Copia la URL pública que imprime Colab al arrancar el bot. Ejemplo:
 *
 *     https://1a2b-3c4d-5e6f.ngrok-free.app
 *
 * Cómo cambiarla en GitHub Pages:
 *   1. Reinicias el bot en Colab → ngrok imprime nueva URL.
 *   2. Vas al repo en GitHub → branch `gh-pages` (o el que uses)
 *      → archivo `config.js` → botón ✏️ Edit → pegas la URL nueva → Commit.
 *   3. GitHub Pages tarda ~1 min en actualizar; refrescas el navegador.
 *
 * Cómo cambiarla en desarrollo local:
 *   Si corres `npm run dev` y el bot en `localhost:5000`, deja el valor
 *   vacío (''): el proxy de Vite redirige solo. Si por el contrario
 *   quieres apuntar el dev al ngrok público, pones aquí esa URL.
 */
window.__BOT_API__ = 'https://stunner-upside-nutty.ngrok-free.dev';
