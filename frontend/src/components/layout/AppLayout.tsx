import { Suspense } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/', label: 'Инстансы' },
  { to: '/definitions', label: 'Определения' },
  { to: '/metrics', label: 'Состояние' },
  { to: '/chaos', label: 'Инъекция отказов' },
]

const navLinkClass = ({ isActive }: { isActive: boolean }): string =>
  [
    'rounded-md px-3 py-1.5 text-sm transition-colors',
    isActive ? 'bg-surface-raised text-white' : 'text-white/60 hover:text-white',
  ].join(' ')

export default function AppLayout() {
  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-6 border-b border-surface-border px-6 py-3">
        <span className="text-sm font-semibold tracking-tight">Workflow Engine</span>
        <nav className="flex gap-1">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={navLinkClass}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="p-6">
        {/* Страницы грузятся по требованию, поэтому место их ожидания нужно:
            без него переход показывал бы пустой экран. */}
        <Suspense
          fallback={<p className="py-6 text-center text-sm text-white/50">Загрузка страницы…</p>}
        >
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
}