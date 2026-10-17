// @vitest-environment jsdom
/**
 * Страница «не найдено» обязана вести обратно, а не быть тупиком.
 *
 * Страница появляется на неверном адресе — и человек, который пришёл по
 * старой ссылке, обязан найти оттуда список процессов. Называть адрес
 * бессмысленно, а вот ссылку «назад» показывать надо.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import NotFoundPage from '@/pages/not-found'

afterEach(cleanup)

describe('страница «не найдено»', () => {
  it('объясняет, что произошло', () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Страница не найдена' })).toBeTruthy()
  })

  it('ведёт обратно к списку процессов', () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Вернуться к списку заказов' })).toBeTruthy()
  })
})