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
    // Пять секунд по умолчанию рассчитаны на чистую логику. Тест страницы под
    // инструментацией покрытия монтирует разметку, ждёт асинхронную загрузку
    // и разбирает её, и на пределе упирается в таймаут — падал файл проверок
    // определений, хотя ни один его тест не был медленным сам по себе.
    testTimeout: 15_000,
    coverage: {
      provider: 'v8',
      // Порог считает только то, что проверяется исполнением. Раньше страницы
      // и компоненты были исключены, потому что исполнять их было нечем: без
      // разметки они не выполнялись, и включение их в отчёт дало бы «100%
      // покрытие» на невыполненном коде. Теперь jsdom есть, список страниц
      // покрыт частично — и это уже настоящая цифра, а не выдуманная.
      include: ['src/lib/**', 'src/store/**', 'src/hooks/**', 'src/api/**', 'src/components/**', 'src/pages/**'],
      reporter: ['text'],
      // Порог держится на достигнутом, а не на завышенном, и сдвинулся вниз не
      // сам по себе: Vitest 4 переписал подсчёт покрытия V8 на анализ AST
      // вместо `v8-to-istanbul`, и прежние 93 % просто не учитывали часть
      // строк. Падение ниже порога по-прежнему означает, что появился
      // непокрытый участок, а не что кто-то сузил include.
      //
      // Долг виден по каталогам: `pages` — 70 % веток, `store` — 70 %. Там
      // остались обработчики ошибок и ветки опроса, до которых не доходит ни
      // один тест. Список файлов не сокращён: под отчётом весь `src`.
      thresholds: {
        statements: 90,
        branches: 80,
        functions: 91,
        lines: 92,
      },
    },
  },
})
