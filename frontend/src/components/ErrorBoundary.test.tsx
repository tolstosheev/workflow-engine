// @vitest-environment jsdom
/**
 * Граница ошибок ловит то, что иначе выглядит как «интерфейс сломался».
 *
 * Без неё необработанное исключение оставляет белый экран без единого слова:
 * неизвестно, упал ли интерфейс или данные. Три вещи проверяются потому, что
 * каждая из них однажды отсутствовала по отдельности: сообщение вместо пустоты,
 * сама ошибка (иначе после замены экрана она потеряна) и возможность попробовать
 * снова — упавший экран без выхода мёртв.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Component } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from '@/components/ErrorBoundary'

afterEach(cleanup)

beforeEach(() => {
  // Компонент пишет ошибку в консоль: без заглушки прогон засоре��ся бы
  // сообщениями, неотличимыми от сбоя самого теста.
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

function Exploding({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error('ответ сервера не разобран')
  return <p>Содержимое на месте</p>
}

describe('граница ошибок', () => {
  it('показывает содержимое, пока всё в порядке', () => {
    render(
      <ErrorBoundary>
        <Exploding shouldThrow={false} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Содержимое на месте')).toBeTruthy()
  })

  it('заменяет упавшую часть экрана объяснением', () => {
    render(
      <ErrorBoundary>
        <Exploding shouldThrow />
      </ErrorBoundary>,
    )

    // Текст должен говорить, что процессы продолжают исполняться: оператор,
    // увидевший ошибку, сначала тревожится о стенде, а не о данных.
    expect(screen.getByRole('alert').textContent).toContain('процессы продолжают исполняться')
  })

  it('показывает саму ошибку, а не только факт падения', () => {
    render(
      <ErrorBoundary>
        <Exploding shouldThrow />
      </ErrorBoundary>,
    )

    expect(screen.getByText('ответ сервера не разобран')).toBeTruthy()
  })

  it('возвращает экран после попытки снова', () => {
    // Ошибка должна быть сбрасываться, иначе страница остаётся заглушкой до
    // перезагрузки — а перезагрузка теряет то самое состояние, из-за которого
    // оператор сюда и пришёл.
    let broken = true

    class Toggle extends Component<{ children?: React.ReactNode }> {
      override render() {
        return <Exploding shouldThrow={broken} />
      }
    }

    render(
      <ErrorBoundary>
        <Toggle />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toBeTruthy()

    broken = false
    fireEvent.click(screen.getByRole('button', { name: 'Попробовать снова' }))

    expect(screen.queryByRole('alert')).toBeNull()
  })
})