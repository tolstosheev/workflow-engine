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
      // Порог считает только то, что проверяется исполнением. Раньше страницы
      // и компоненты были исключены, потому что исполнять их было нечем: без
      // разметки они не выполнялись, и включение их в отчёт дало бы «100%
      // покрытие» на невыполненном коде. Теперь jsdom есть, список страниц
      // покрыт частично — и это уже настоящая цифра, а не выдуманная.
      include: ['src/lib/**', 'src/store/**', 'src/hooks/**', 'src/api/**', 'src/components/**', 'src/pages/**'],
      reporter: ['text'],
      // Порог по строкам стоит низко не потому, что 46 % — норма, а потому что
      // раньше страницы и компоненты вообще не попадали в отчёт: при них
      // цифра была 91 % на половине файлов. Теперь отчёт честный, и видно,
      // сколько ещё не покрыто.
      //
      // Не покрыты: pages/definitions.tsx, pages/instance.tsx,
      // pages/not-found.tsx, components/DefinitionGraph.tsx,
      // components/RollbackLane.tsx, components/WorkerLeases.tsx,
      // components/layout/AppLayout.tsx, api/stream.ts. Это следующая
      // очередь: без неё порог по строкам не поднялся бы.
      thresholds: {
        statements: 45,
        branches: 80,
        functions: 80,
        lines: 45,
      },
    },
  },
})
