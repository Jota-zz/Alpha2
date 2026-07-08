/**
 * App: layout principal con sidebar fija + TopBar + main scrollable + Toasts.
 * Las 5 páginas están conectadas a sus rutas.
 *
 * `basename`: Vite expone `import.meta.env.BASE_URL` que coincide con el
 * `base` configurado en vite.config.js. Lo pasamos a BrowserRouter para
 * que en GitHub Pages (https://USER.github.io/REPO/) las rutas internas
 * resuelvan a /REPO/argos, /REPO/configuracion, etc.
 */

import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Sidebar } from './components/layout/Sidebar';
import { TopBar } from './components/layout/TopBar';
import { ToastHost } from './components/ui/ToastHost';
import { DashboardPage } from './pages/Dashboard';
import { ArgosPage } from './pages/Argos';
import { ConfiguracionPage } from './pages/Configuracion';
import { BroadcastsPage } from './pages/Broadcasts';
import { LogsPage } from './pages/Logs';

// BASE_URL llega con slash final ('/REPO/' o '/'). BrowserRouter quiere
// el basename SIN slash final, así que se lo quitamos.
const basename = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') || '/';

export default function App() {
  return (
    <BrowserRouter basename={basename}>
      <div className="app-shell relative">
        <div className="fixed inset-0 bg-grid pointer-events-none" />

        <Sidebar />

        <div className="flex flex-col min-h-screen relative">
          <TopBar />
          <main className="main-content flex-1">
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/argos" element={<ArgosPage />} />
              <Route path="/configuracion" element={<ConfiguracionPage />} />
              <Route path="/broadcasts" element={<BroadcastsPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route
                path="*"
                element={
                  <div className="empty-state">
                    <div className="empty-state-icon">🤔</div>
                    <div className="empty-state-title">Página no encontrada</div>
                  </div>
                }
              />
            </Routes>
          </main>
        </div>

        <ToastHost />
      </div>
    </BrowserRouter>
  );
}
