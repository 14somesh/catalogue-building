import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/brands': 'http://127.0.0.1:8000',
      '/products': 'http://127.0.0.1:8000',
      '/uploads': 'http://127.0.0.1:8000',
      '/ingest': 'http://127.0.0.1:8000',
      '/build': {
        target: 'http://127.0.0.1:8000',
        bypass: (req) => {
          if (req.method === 'GET' && req.headers.accept && req.headers.accept.includes('text/html')) {
            return '/index.html';
          }
        }
      },
      '/builds': 'http://127.0.0.1:8000',
      '/jobs': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/images': 'http://127.0.0.1:8000',
      '/dist': 'http://127.0.0.1:8000',
      '/brochures': 'http://127.0.0.1:8000',
      '/validate': 'http://127.0.0.1:8000',
      '/config': 'http://127.0.0.1:8000',
      '/categories': 'http://127.0.0.1:8000'
    }
  }
})
