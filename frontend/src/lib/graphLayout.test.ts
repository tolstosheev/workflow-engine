/** Тесты раскладки графа — чистая логика без DOM и React. */

import { describe, expect, it } from 'vitest'

import { backEdgePath, layoutGraph } from '@/lib/graphLayout'
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

  it('не растягивает слои из-за обратного ребра', () => {
    // Регрессия: обратное ребро участвовало в раздаче слоёв, и предел обхода
    // останавливал раскладку на возросших значениях — цикл из трёх шагов
    // растягивал схему до седьмого слоя, оставляя между первым узлом и
    // остальными пустую полосу в полтора экрана.
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:validate_cart`, next: 'b' },
        b: { action: `${DEMO}:reserve_inventory`, next: 'c' },
        c: { action: `${DEMO}:charge_payment`, next: 'a' },
      }),
    )

    const layerOf = (name: string) => layout.nodes.find((node) => node.name === name)?.layer

    expect(layerOf('a')).toBe(0)
    expect(layerOf('b')).toBe(1)
    expect(layerOf('c')).toBe(2)
  })

  it('уводит обратное ребро в полосу под узлами', () => {
    // Регрессия: ребро шло от правого края источника к левому краю цели по
    // линии центров. У цикла оба узла на одной строке, точки безье
    // коллинеарны, кривая вырождается в прямую поверх прямого ребра, и
    // обе стрелки упирались в один левый край.
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:reserve_inventory`, next: 'b' },
        b: { action: `${DEMO}:charge_payment`, next: 'a' },
      }),
    )

    const from = layout.nodes.find((node) => node.name === 'b')
    const to = layout.nodes.find((node) => node.name === 'a')
    if (!from || !to) throw new Error('нет узлов цикла')

    expect(layout.laneY).not.toBeNull()
    const laneY = layout.laneY ?? 0
    expect(laneY).toBeGreaterThan(Math.max(...layout.nodes.map((node) => node.y + node.height)))
    expect(laneY).toBeLessThan(layout.height)

    const path = backEdgePath(from, to, laneY)
    // Выход из нижней середины источника и вход в нижнюю середину цели:
    // на линии центров ребро пересекало промежуток между узлами.
    expect(path).toContain(`M ${from.x + from.width / 2} ${from.y + from.height}`)
    expect(path.endsWith(`L ${to.x + to.width / 2} ${to.y + to.height}`)).toBe(true)
    // Ни одна точка пути не лежит на линии центров узлов (высота 68 при
    // y=32, центр — 66).
    expect(path).not.toMatch(/(^|\s)66(\s|$)/)
    expect(path).toContain(` ${laneY} `)
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

  it('не рисует компенсацию ребром в самого себя', () => {
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:reserve_inventory`, compensation: `${DEMO}:release_inventory` },
      }),
    )

    // Стрелка «шаг → тот же шаг» читалась как повтор шага, то есть как цикл, и
    // спрашивающий «почему шаг указывает сам на себя» получал ответ, которого
    // рисунок не давал. Обработчик компенсации — свойство узла, а не ребро.
    expect(layout.edges).toHaveLength(0)
    expect(layout.edges.every((edge) => edge.from !== edge.to)).toBe(true)
  })

  it('компенсация остаётся видна на узле', () => {
    const layout = layoutGraph(
      definition('a', {
        a: { action: `${DEMO}:reserve_inventory`, compensation: `${DEMO}:release_inventory` },
      }),
    )

    expect(layout.nodes[0].step.compensation).toBe(`${DEMO}:release_inventory`)
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

    expect(layout).toEqual({ nodes: [], edges: [], width: 0, height: 0, laneY: null })
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

  it('разводит петлю шага на себя по полосе, а не в нулевую ширину', () => {
    // Схема допускает шаг, ведущий на себя: ребро уходит вниз по той же
    // колонке, и закругления самоопределялись бы в обе стороны.
    const layout = layoutGraph(
      definition('a', { a: { action: `${DEMO}:reserve_inventory`, next: 'a' } }),
    )

    const node = layout.nodes[0]
    const path = backEdgePath(node, node, layout.laneY ?? 0)

    expect(path).toContain(`M ${node.x + node.width / 2} ${node.y + node.height}`)
    expect(path.endsWith(`L ${node.x + node.width / 2} ${node.y + node.height}`)).toBe(true)
    expect(path).not.toMatch(/NaN/)
  })
})