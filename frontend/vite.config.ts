import fs from 'node:fs';
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Vite configuration for StageState Frontend.
 *
 * Proxies `/api` and dynamic `/assets` requests to the local FastAPI backend
 * (`http://localhost:8000`) during development, while bypassing proxy for static
 * files already present under `frontend/public` so offline preview never 500s.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'three-vendor': ['three'],
          'react-vendor': ['react', 'react-dom'],
        },
      },
    },
  },
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
        bypass(req) {
          const reqPath = (req.url || '').split('?')[0];
          const localFile = path.resolve(__dirname, 'public', reqPath.replace(/^\/+/, ''));
          if (fs.existsSync(localFile) && fs.statSync(localFile).isFile()) {
            return req.url;
          }
          return undefined;
        },
      },
      '/shared-assets': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
