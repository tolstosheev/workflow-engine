// @vitest-environment jsdom
/**
 * Каркас: куда можно уйти и что видно о текущем разделе.
 *
 * Активный раздел отмечался только цветом ссылки: с клавиатуры и для
 * скринридера не было видно, где человек находится, — а на странице метрик
 * это разница между «смотрю дашборд» и «смотрю пустое место».
 *
 * Страницы грузятся по требованию, поэтому у каркаса есть заглушка: без неё
 * переход показывал бы пустой экран, и пустой экран неотличим от зависшего.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { lazy } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import AppLayout from '@/components/layout/AppLayout'

/** Раздел грузится по требованию — как это делает настоящий маршрут. */
const LazySection = lazy(async () => ({ default: () => <p>Содержимое раздела</p> }))

afterEach(cleanup)

describe('каркас', () => {
  it('показывает заглушку, пока раздел грузится, и потом его содержимое', async () => {
    // Разделы загружаются по требованию, и без заглушки переход выглядел бы
    // как зависший интерфейс, а не как ожидание.
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<LazySection />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('Загрузка страницы…')).toBeTruthy()
    expect(await screen.findByText('Содержимое раздела')).toBeTruthy()
  })

  it('ведёт во все разделы', () => {
    render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>,
    )

    for (const name of ['Инстансы', 'Определения', 'Состояние', 'Инъекция отказов']) {
      expect(screen.getByRole('link', { name })).toBeTruthy()
    }
  })

  it('показывает ссылку на главную как текущую, а не как обычную', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <AppLayout />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Инстансы' })

    // Главная совпадает сама с собой, и без точной привязки её активность
    // вычислялась бы иначе, чем у остальных разделов.
    expect(link.getAttribute('aria-current')).toBe('page')
  })

  it('отмечает раздел по текущему пути', () => {
    render(
      <MemoryRouter initialEntries={['/metrics']}>
        <AppLayout />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Состояние' }).getAttribute('aria-current')).toBe(
      'page',
    )
    expect(screen.getByRole('link', { name: 'Инстансы' }).getAttribute('aria-current')).toBeNull()
  })
})