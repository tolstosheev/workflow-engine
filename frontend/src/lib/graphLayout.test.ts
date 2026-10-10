/** Тесты раскладки графа — чистая логика без DOM и React. */

import { describe, expect, it } from 'vitest'

import { layoutGraph } from '@/lib/graphLayout'
import type { WorkflowDefinitionSchema } from '@/types/api'

const DEMO = 'app.demo.handlers'

function definition(
  start: string,
  steps: WorkflowDefinitionSchema['steps'],
): WorkflowDefinitionSchema {
  return { start, steps }
}

describe('layoutGraph', () => {
  it('раздаёт слои слева направо по направлению исполнения', () => {
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:validate_cart`, next: 'b' },
        b: { action: `${DEMO}:reserve_inventory`, next: 'c' },
        c: { action: `${DEMO}:confirm_delivery` },
      }),
    )

    const layerOf = (name: string) => layout.nodes.find((node) => node.name === name)?.layer

    expect(layerOf('a')).toBe(0)
    expect(layerOf('b')).toBe(1)
    expect(layerOf('c')).toBe(2)
  })

  it('разводит ветви по разным слоям', () => {
    const layout = layoutGraph(
      definition('start', {
        start: { action: `${DEMO}:validate_cart`, next: { on_success: 'a', on_failure: 'b' } },
        a: { action: `${DEMO}:reserve_inventory` },
        b: { action: `${DEMO}:charge_payment` },
      }),
    )

    expect(layout.edges).toContainEqual(
      expect.objectContaining({ from: 'start', to: 'a', kind: 'branch', label: 'on_success' }),
    )
    expect(layout.edges).toContainEqual(
      expect.objectContaining({ from: 'start', to: 'b', kind: 'branch', label: 'on_failure' }),
    )
  })

  it('помечает обратное ребро цикла, не замыкая раскладку', () => {
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:reserve_inventory`, next: 'b' },
        b: { action: `${DEMO}:charge_payment`, next: 'a' },
      }),
    )

    // Ребро a->b идёт по слоям, b->a замыкает цикл и помечено обратным.
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'a', to: 'b', kind: 'next' }))
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'b', to: 'a', kind: 'back' }))
  })

  it('завершает раскладку графа с циклом, а не зависает', () => {
    // Регрессия: обход цикла обязан быть конечным. Шаг, ведущий в предка,
    // помечается обратным и перестаёт участвовать в обходе.
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:validate_cart`, next: 'b' },
        b: { action: `${DEMO}:reserve_inventory`, next: 'c' },
        c: { action: `${DEMO}:charge_payment`, next: 'a' },
      }),
    )

    expect(layout.nodes).toHaveLength(3)
    expect(layout.edges.filter((edge) => edge.kind === 'back')).toHaveLength(1)
  })

  it('рисует недостижимый шаг вместо того, чтобы его терять', () => {
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:confirm_delivery` },
        orphan: { action: `${DEMO}:validate_cart`, next: 'another' },
        another: { action: `${DEMO}:reserve_inventory` },
      }),
    )

    const orphan = layout.nodes.find((node) => node.name === 'orphan')
    const another = layout.nodes.find((node) => node.name === 'another')

    // Валидатор такую часть отвергает, но если она всё же есть, она должна
    // быть видна: потерянный шаг в схеме читается как потеря данных.
    expect(layout.nodes).toHaveLength(3)
    expect(layout.edges).toContainEqual(expect.objectContaining({ from: 'orphan', to: 'another' }))
    // Своя компонента начинается с нулевого слоя, порядок внутри неё цел.
    expect(orphan?.layer).toBe(0)
    expect(another?.layer).toBe(1)
  })

  it('не роняется на переходе в неизвестный шаг', () => {
    // Валидатор такое отвергает, но отрисовка не должна падать на мусоре:
    // пустой экран хуже схемы с недостающим узлом.
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:confirm_delivery`, next: 'missing' },
      }),
    )

    expect(layout.nodes).toHaveLength(1)
    expect(layout.edges.some((edge) => edge.to === 'missing')).toBe(false)
  })

  it('изображает компенсацию на самом шаге, а не ребром в пустоту', () => {
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:reserve_inventory`, compensation: `${DEMO}:release_inventory` },
      }),
    )

    const compensation = layout.edges.find((edge) => edge.kind === 'compensation')

    // Обработчик компенсации не узел графа, поэтому ребро ведёт в себя же.
    expect(compensation).toMatchObject({
      from: 'a',
      to: 'a',
      label: `${DEMO}:release_inventory`,
    })
  })

  it('разводит узлы одного слоя по вертикали', () => {
    const layout = layoutGraph(
      definition('start', {
        start: { action: `${DEMO}:validate_cart`, next: { on_success: 'a', on_failure: 'b' } },
        a: { action: `${DEMO}:reserve_inventory` },
        b: { action: `${DEMO}:charge_payment` },
      }),
    )

    const a = layout.nodes.find((node) => node.name === 'a')
    const b = layout.nodes.find((node) => node.name === 'b')

    expect(a?.layer).toBe(b?.layer)
    expect(a?.y).not.toBe(b?.y)
  })

  it('пустое определение даёт пустую раскладку без ошибок', () => {
    const layout = layoutGraph(definition('a', {} as WorkflowDefinitionSchema['steps']))

    expect(layout).toEqual({ nodes: [], edges: [], width: 0, height: 0 })
  })

  it('подставляет первый шаг, если start указывает в пустоту', () => {
    const layout = layoutGraph(
      definition('missing', {
        real: { action: `${DEMO}:confirm_delivery` },
      }),
    )

    expect(layout.nodes).toHaveLength(1)
    expect(layout.nodes[0].layer).toBe(0)
  })
})