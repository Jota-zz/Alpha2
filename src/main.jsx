/**
 * Entry point del frontend.
 *
 * Setup:
 *   - StrictMode para detectar side effects en dev.
 *   - QueryClient con defaults razonables para la app.
 *   - Importa el design-system.css (que a su vez carga Tailwind v4).
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './styles/design-system.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // staleTime de 30s por defecto. Los hooks que necesitan más frescura
      // (status, logs) lo overridean a 4s.
      staleTime: 30_000,
      // No refetchOnWindowFocus por defecto: para evitar que cambiar de
      // pestaña dispare N refetches simultáneos. Los hooks que lo necesitan
      // lo activan explícitamente.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
);
