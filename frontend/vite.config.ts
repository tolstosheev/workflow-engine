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
    // Чистая логика проверяется без DOM, а компонентные тесты требуют
    // разметки: окружение jsdom включается по файлу-признаку `.tsx`,
    // чтобы логика оставалась быстрой.
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    coverage: {
      provider: 'v8',
      // Порог считает только то, что проверяется исполнением: страницы и
      // компоненты покрываются тестами компонентов, а иначе включение их в
      // отчёт дало бы «100% покрытие» на невыполненном коде.
      include: ['src/lib/**', 'src/store/**', 'src/api/client.ts'],
      reporter: ['text'],
      thresholds: {
        statements: 80,
        branches: 80,
        functions: 80,
        lines: 80,
      },
    },
  },
})
