// @vitest-environment jsdom
/**
 * Живая лента: подписка и разрыв соединения.
 *
 * Три свойства потока ломались по отдельности, и каждое тихо:
 *
 * - кадр, пришедший повторно или с опозданием, возвращал завершённый процесс в
 *   «выполняется»: `EventSource` при переподключении начинает с последнего
 *   события, и кадры действительно повторяются;
 * - подписка на тип, которого поток не шлёт, оставляла обработчик в редьюсере
 *   недостижимым — список типов живёт в двух файлах и разъезжается молча;
 * - при уходе со страницы источник обязан закрыться, иначе на каждое
 *   перемонтирование заводится новый поток, и вкладка размножает их без счёта.
 *
 * DOM нужен ради `renderHook`; сама проверка — про `EventSource`, подменённый
 * заглушкой: ни сети, ни таймеров.
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SUBSCRIBED_EVENT_TYPES, useEventStream } from '@/api/stream'
import type { StreamEvent } from '@/types/api'

/** Заглушка источника событий: помнит подписки и умеет отдать кадр. */
class FakeEventSource {
  static last: FakeEventSource | null = null

  readonly url: string
  readonly listeners = new Map<string, Array<(raw: MessageEvent) => void>>()
  closed = false
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }

  addEventListener(type: string, listener: EventListener): void {
    const existing = this.listeners.get(type) ?? []
    existing.push(listener as (raw: MessageEvent) => void)
    this.listeners.set(type, existing)
  }

  close(): void {
    this.closed = true
  }

  /** Отдаёт кадр всем, кто подписан на этот тип. */
  emit(type: string, payload: unknown): void {
    const raw = { data: JSON.stringify(payload) } as MessageEvent
    for (const listener of this.listeners.get(type) ?? []) listener(raw)
  }

  open(): void {
    this.onopen?.()
  }

  break(): void {
    this.onerror?.()
  }
}

function event(over: Partial<StreamEvent> = {}): StreamEvent {
  return {
    instance_id: 'i-1',
    seq: 1,
    type: 'STEP_COMPLETED',
    payload: { step: 'reserve_inventory' },
    created_at: '2026-03-01T10:00:00Z',
    ...over,
  } as StreamEvent
}

beforeEach(() => {
  FakeEventSource.last = null
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('подписка на поток', () => {
  it('подписывается ровно на объявленные типы', () => {
    renderHook(() => useEventStream({ onEvent: vi.fn() }))

    expect([...FakeEventSource.last!.listeners.keys()].sort()).toEqual(
      [...SUBSCRIBED_EVENT_TYPES].sort(),
    )
  })

  it('не заводит новый источник на каждый рендер', () => {
    const { rerender } = renderHook(
      ({ handler }: { handler: (event: StreamEvent) => void }) =>
        useEventStream({ onEvent: handler }),
      { initialProps: { handler: vi.fn() } },
    )
    const first = FakeEventSource.last

    rerender({ handler: vi.fn() })
    rerender({ handler: vi.fn() })

    // На стенде список открывал по потоку на каждое событие: смена обработчика
    // приходит с каждым кадром, и подписка рвалась бы вместе с ней.
    expect(FakeEventSource.last).toBe(first)
    expect(first?.closed).toBe(false)
  })

  it('передаёт событие вызывающему', () => {
    const onEvent = vi.fn()
    renderHook(() => useEventStream({ onEvent }))

    act(() => FakeEventSource.last!.emit('STEP_COMPLETED', event()))

    expect(onEvent).toHaveBeenCalledOnce()
    expect(onEvent.mock.calls[0][0]).toMatchObject({ type: 'STEP_COMPLETED', seq: 1 })
  })

  it('не рвёт поток на повреждённом кадре', () => {
    const onEvent = vi.fn()
    renderHook(() => useEventStream({ onEvent }))

    const source = FakeEventSource.last!
    act(() => {
      source.listeners.get('STEP_COMPLETED')![0]({ data: '{не json' } as MessageEvent)
      source.emit('STEP_COMPLETED', event())
    })

    // Одна битая запись в журнале не должна отключать весь экран до
    // перезагрузки: следующий кадр приходит вовремя и обрабатывается.
    expect(onEvent).toHaveBeenCalledOnce()
  })

  it('вызывает последний обработчик, а не тот, что был при подписке', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = renderHook(
      ({ handler }: { handler: (event: StreamEvent) => void }) =>
        useEventStream({ onEvent: handler }),
      { initialProps: { handler: first } },
    )

    rerender({ handler: second })
    act(() => FakeEventSource.last!.emit('STEP_COMPLETED', event()))

    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledOnce()
  })

  it('закрывает источник при уходе со страницы', () => {
    const { unmount } = renderHook(() => useEventStream({ onEvent: vi.fn() }))
    const source = FakeEventSource.last!

    unmount()

    expect(source.closed).toBe(true)
  })

  it('сообщает о разрыве и о восстановлении связи', () => {
    const onStatusChange = vi.fn()
    renderHook(() => useEventStream({ onEvent: vi.fn(), onStatusChange }))

    act(() => FakeEventSource.last!.open())
    act(() => FakeEventSource.last!.break())

    expect(onStatusChange.mock.calls).toEqual([[true], [false]])
  })

  it('работает без объявленного обработчика статуса', () => {
    // Отсутствие колбэка не должно рвать подписку: вызов без проверки упал бы
    // на первом же событии.
    renderHook(() => useEventStream({ onEvent: vi.fn() }))

    expect(() => act(() => FakeEventSource.last!.open())).not.toThrow()
  })
})