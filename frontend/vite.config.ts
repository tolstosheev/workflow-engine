import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API_URL = process.env.VITE_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Алиас дублирует paths из tsconfig.json: TypeScript резолвит импорты
    // сам, а Vite о них не знает.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
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
  test: {
    // Окружение jsdom нужен будущим тестам компонентов; сейчас проверяется
    // чистая логика раскладки, для неё достаточно node.
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
