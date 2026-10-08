import { createBrowserRouter } from 'react-router-dom'

import AppLayout from '@/components/layout/AppLayout'
import DefinitionsPage from '@/pages/definitions'
import InstancePage from '@/pages/instance'
import InstancesPage from '@/pages/instances'
import NotFoundPage from '@/pages/not-found'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <InstancesPage /> },
      { path: 'instances/:id', element: <InstancePage /> },
      { path: 'definitions', element: <DefinitionsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
