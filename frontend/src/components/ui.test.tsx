// @vitest-environment jsdom
/**
 * Кнопка панели не должна отправлять форму.
 *
 * `type` у кнопки по умолчанию равен `submit`, и нажатие на кнопку внутри
 * формы отправило бы форму вместо своего действия: на странице инстанса
 * действие — отменить процесс, а отправка формы перезагрузила бы страницу
 * вместе с открытым редактором определения. Поведение выглядит безобидно, пока
 * рядом не окажется форма.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Button } from '@/components/ui'

afterEach(cleanup)

/** Запрос отдаёт `HTMLElement`, а кнопка — это `HTMLButtonElement`. */
function button(name: string): HTMLButtonElement {
  return screen.getByRole('button', { name }) as HTMLButtonElement
}

describe('кнопка', () => {
  it('не отправляет форму по умолчанию', () => {
    render(<Button>Обычное действие</Button>)

    expect(button('Обычное действие').type).toBe('button')
  })

  it('отправляет форму только когда сказано', () => {
    render(
      <form>
        <Button type="submit">Отправить</Button>
      </form>,
    )

    expect(button('Отправить').type).toBe('submit')
  })

  it('показывает, что действие сейчас недоступно', () => {
    render(<Button disabled>Запустить</Button>)

    expect(button('Запустить').disabled).toBe(true)
  })
})