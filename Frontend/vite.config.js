import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * Build config.
 *
 * `base` controla el prefijo de las URLs de los assets en el bundle final.
 *  - En desarrollo (`npm run dev`): siempre '/'.
 *  - En GitHub Pages bajo subpath (https://USER.github.io/REPO/): tiene
 *    que ser '/REPO/'. Lo defines con la env var VITE_BASE al hacer
 *    `npm run build` (ver workflow .github/workflows/deploy.yml).
 *  - Si despliegas en un dominio propio o en la raíz: dejas '/'.
 */
export default defineConfig(({ command }) => {
  const isDev = command === 'serve';
  const base =
    isDev ? '/' : (process.env.VITE_BASE || '/');

  return {
    base,
    plugins: [react(), tailwindcss()],

    server: {
      port: 5173,
      host: true,
      // Proxy hacia el bot Flask durante dev. Esto evita CORS local
      // cuando corres `npm run dev` y el bot en otra terminal.
      // Si tu bot expone otro puerto, ajusta `target`.
      proxy: {
        '/api': {
          target: 'http://localhost:5000',
          changeOrigin: true,
        },
        '/webhook': {
          target: 'http://localhost:5000',
          changeOrigin: true,
        },
        '/health': {
          target: 'http://localhost:5000',
          changeOrigin: true,
        },
      },
    },

    build: {
      outDir: 'dist',
      // Plotly es enorme (~3MB minified). Lo dejamos en su propio chunk
      // para que el resto del bundle cargue rápido y Plotly se cachee aparte.
      rollupOptions: {
        output: {
          manualChunks: {
            plotly: ['plotly.js-dist-min', 'react-plotly.js'],
          },
        },
      },
    },
  };
});
