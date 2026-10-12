import { lazy } from 'react'
import { createBrowserRouter } from 'react-router-dom'

import AppLayout from '@/components/layout/AppLayout'

/**
 * Маршруты грузятся по требованию.
 *
 * Все шесть страниц попадали в первый чанк, хотя открывается одна: панель
 * инъекций и разбор метрик нужны при разборе стенда, а не при работе с
 * заказами. Главная остаётся в основном чанке — она и есть вход.
 */
const InstancesPage = lazy(() => import('@/pages/instances'))
const InstancePage = lazy(() => import('@/pages/instance'))
const DefinitionsPage = lazy(() => import('@/pages/definitions'))
const MetricsPage = lazy(() => import('@/pages/metrics').then((m) => ({ default: m.MetricsPage })))
const ChaosPage = lazy(() => import('@/pages/chaos'))
const NotFoundPage = lazy(() => import('@/pages/not-found'))

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <InstancesPage /> },
      { path: 'instances/:id', element: <InstancePage /> },
      { path: 'definitions', element: <DefinitionsPage /> },
      { path: 'metrics', element: <MetricsPage /> },
      { path: 'chaos', element: <ChaosPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])