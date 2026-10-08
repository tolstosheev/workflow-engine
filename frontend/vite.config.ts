import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API_URL = process.env.VITE_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Прокси только /api: SSE через EventSource не умеет слать заголовки,
    // а CORS на бэкенде настроен на конкретные origins.
    proxy: {
      '/api': {
        target: API_URL,
        changeOrigin: true,
      },
    },
  },
})
