import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Vite configuration for StageState Frontend.
 *
 * Proxies `/api` requests to the local FastAPI backend (`http://localhost:8000`)
 * during development while serving static assets from `frontend/public`.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/assets': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/shared-assets': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
