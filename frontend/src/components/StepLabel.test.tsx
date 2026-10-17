// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { StepLabel } from './StepLabel'

afterEach(cleanup)

describe('StepLabel', () => {
  it('показывает описание, а имя оставляет вторым текстом', () => {
    render(
      <StepLabel
        title={{ title: 'Списание средств — главный необратимый шаг', identifier: 'charge_payment' }}
      />,
    )

    expect(screen.getByText('Списание средств — главный необратимый шаг')).toBeTruthy()
    expect(screen.getByText('charge_payment')).toBeTruthy()
  })

  it('у шага без описания не выдумывает вторую строку', () => {
    render(<StepLabel title={{ title: 'reserve', identifier: null }} />)

    expect(screen.getByText('reserve')).toBeTruthy()
    expect(screen.queryByText('reserve', { selector: '.font-mono' })).toBeNull()
  })

  it('без шага не рисует ничего, а не пустой блок', () => {
    const { container } = render(<StepLabel title={null} />)

    expect(container.textContent).toBe('')
  })
})