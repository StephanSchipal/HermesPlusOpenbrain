import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-server proxy so `fetch('/api/...')` works identically in dev (Vite
// on :5173, backend on :8000) and in production (same origin, one
// container) -- no environment-specific API base URL needed anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
