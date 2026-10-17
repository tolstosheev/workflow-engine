import { describe, expect, it } from 'vitest'

import { describeEvent, describeStreamEvent } from './events'
import { stepResolver } from './steps'

describe('фраза о событии', () => {
  it('называет шаг по описанию из схемы, когда подмена передана', () => {
    // Фраза живёт в журнале на странице, где схема уже загружена, а имя шага
    // рядом с подписью «Платёж отклонён» читается как служебная выдача.
    const resolve = stepResolver({
      reserve_inventory: { action: 'a', description: 'Резерв товара на складе' },
      charge_payment: { action: 'b', description: 'Списание средств' },
    })

    expect(
      describeEvent(
        { type: 'STEP_COMPLETED', payload: { step: 'reserve_inventory', next: 'charge_payment' } },
        resolve,
      ),
    ).toBe('Шаг Резерв товара на складе выполнен, дальше Списание средств')
  })

  it('без подстановки оставляет имя: без схемы фраза обязана собираться', () => {
    expect(
      describeEvent({
        type: 'COMPENSATION_FAILED',
        payload: { for_step: 'reserve_inventory', attempt: 2, error: 'склад недоступен' },
      }),
    ).toBe('Компенсация шага reserve_inventory не выполнена на попытке 2: склад недоступен')
  })

  it('называет шаг и следующий переход', () => {
    expect(
      describeEvent({ type: 'STEP_COMPLETED', payload: { step: 'reserve_inventory', next: 'charge_payment' } }),
    ).toBe('Шаг reserve_inventory выполнен, дальше charge_payment')
  })

  it('называет шаг, попытку и причину отказа', () => {
    expect(
      describeEvent({ type: 'STEP_FAILED', payload: { step: 'charge_payment', attempt: 3, error: 'отказ банка' } }),
    ).toBe('Шаг charge_payment не выполнен на попытке 3: отказ банка')
  })

  it('различает срок ожидания человека и ожидание шага', () => {
    // Срок паузы планируется тем же событием, что и ожидание шага. Без разбора
    // по виду одно состояние называлось двумя словами, и это выглядело как
    // ошибка на экране, а не как совпадение в реализации.
    const pause = describeEvent({
      type: 'TIMER_SCHEDULED',
      payload: { step: 'approve', kind: 'pause_deadline', due_at: '2026-03-01T10:00:00Z' },
    })
    const wait = describeEvent({
      type: 'TIMER_SCHEDULED',
      payload: { step: 'approve', kind: 'step', due_at: '2026-03-01T10:00:00Z' },
    })

    expect(pause).toContain('срок ожидания решения')
    expect(wait).not.toContain('срок ожидания решения')
    expect(wait).toContain('ожидание')
  })

  it('показывает, сколько проходов цикла осталось', () => {
    expect(
      describeEvent({ type: 'LOOP_EXHAUSTED', payload: { step: 'items', iterations: 3, max_iterations: 10 } }),
    ).toBe('Шаг items: исчерпаны проходы цикла — 3 из 10 проходов')
  })

  it('объясняет откат через обработчик компенсации', () => {
    expect(
      describeEvent({
        type: 'COMPENSATION_STARTED',
        payload: { for_step: 'reserve_inventory', compensation: 'app.demo:release_inventory', attempt: 1 },
      }),
    ).toBe(
    'Компенсация начата: для шага reserve_inventory вызывается app.demo:release_inventory на попытке 1',
  )
  })

  it('говорит, какой компенсации не удалось и почему', () => {
    expect(
      describeEvent({
        type: 'COMPENSATION_FAILED',
        payload: { for_step: 'reserve_inventory', attempt: 2, error: 'склад недоступен' },
      }),
    ).toBe('Компенсация шага reserve_inventory не выполнена на попытке 2: склад недоступен')
  })

  it('не придумывает отсутствующую попытку', () => {
    // Попытки в payload может не быть: тогда фраза «на попытке 0» была бы
    // выдумкой интерфейса, которой нет в журнале.
    expect(describeEvent({ type: 'STEP_FAILED', payload: { step: 'validate_cart' } })).toBe(
      'Шаг validate_cart не выполнен',
    )
  })

  it('компенсируется к подписи, когда имя шага неизвестно', () => {
    // «Шаг ?» на экране читается как поломка интерфейса. Подпись события
    // ничего не выдумывает и показывает, что известно точно.
    expect(describeEvent({ type: 'STEP_STARTED' })).toBe('Шаг начат')
    expect(describeEvent({ type: 'COMPENSATION_COMPLETED', payload: {} })).toBe('Компенсация выполнена')
  })

  it('не падает на мусоре вместо payload', () => {
    expect(describeEvent({ type: 'INSTANCE_COMPLETED', payload: 'не объект' })).toBe(
      'Процесс завершён: все шаги выполнены',
    )
    expect(describeEvent({ type: 'STEP_COMPLETED', payload: null })).toBe('Шаг выполнен')
  })

  it('называет начало процесса, начало шага и выполненную компенсацию', () => {
    expect(describeEvent({ type: 'INSTANCE_CREATED' })).toBe('Процесс создан')
    expect(describeEvent({ type: 'STEP_STARTED', payload: { step: 'reserve_inventory', attempt: 1 } })).toBe(
      'Шаг reserve_inventory, попытка 1',
    )
    expect(
      describeEvent({ type: 'COMPENSATION_COMPLETED', payload: { for_step: 'reserve_inventory', attempt: 1 } }),
    ).toBe('Компенсация шага reserve_inventory выполнена на попытке 1')
  })

  it('разворачивает паузу и её снятие', () => {
    expect(describeEvent({ type: 'INSTANCE_PAUSED', payload: { step: 'approve' } })).toBe(
      'Шаг approve: процесс ждёт решения человека',
    )
    expect(describeEvent({ type: 'INSTANCE_RESUMED', payload: { step: 'approve' } })).toBe(
      'Шаг approve: ожидание снято, процесс продолжен',
    )
  })

  it('называет следующую попытку при повторе', () => {
    expect(
      describeEvent({ type: 'STEP_RETRY_SCHEDULED', payload: { step: 'charge_payment', attempt: 2, error: 'таймаут' } }),
    ).toBe('Шаг charge_payment поставлен на повтор, следующая попытка 2: таймаут')
  })

  it('говорит про сработавшее ожидание и про отмену', () => {
    expect(describeEvent({ type: 'TIMER_FIRED', payload: { step: 'await_delivery' } })).toBe(
      'Шаг await_delivery: ожидание сработало',
    )
    expect(describeEvent({ type: 'INSTANCE_CANCEL_REQUESTED', payload: { reason: 'оператор' } })).toBe(
      'Запрошена отмена процесса: оператор',
    )
    expect(describeEvent({ type: 'INSTANCE_CANCELLED' })).toBe('Процесс отменён')
  })

  it('объясняет итоговые состояния по-разному', () => {
    // Три итоговых состояния похожи по форме и разны по смыслу: без пояснения
    // «завершился ошибкой» читается как «всё упало», хотя компенсировать было
    // нечего и процесс вёл себя правильно.
    expect(describeEvent({ type: 'INSTANCE_COMPENSATED' })).toContain(
      'возвращены в исходное состояние',
    )
    expect(describeEvent({ type: 'INSTANCE_FAILED' })).toContain('компенсировать было нечего')
    expect(describeEvent({ type: 'INSTANCE_COMPLETED' })).toContain('все шаги выполнены')
  })

  it('обходится без срока ожидания', () => {
    expect(describeEvent({ type: 'TIMER_SCHEDULED', payload: { step: 'approve' } })).toBe('Шаг approve: ожидание')
  })

  it('читает событие из потока тем же способом', () => {
    const event = {
      instance_id: 'i-1',
      seq: 7,
      type: 'STEP_COMPLETED' as const,
      payload: { step: 'reserve_inventory', next: 'charge_payment' },
      created_at: '2026-03-01T10:00:00Z',
    }
    expect(describeStreamEvent(event)).toBe(
      'Шаг reserve_inventory выполнен, дальше charge_payment',
    )
  })

  it('показывает подпись, когда тип события неизвестен движку', () => {
    // Событие, которого словарь ещё не знает, должно быть видно, а не
    // исчезнуть: журнал — источник истины, и пробел в нём хуже лишней строки.
    expect(describeEvent({ type: 'STEP_SKIPPED', payload: {} })).toBe('STEP_SKIPPED')
  })
})
