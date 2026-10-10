/**
 * Раскладка графа определения в координаты.
 *
 * Чистая функция без React и без DOM: раскладку удобно проверять тестами, и
 * она меняется отдельно от отрисовки.
 *
 * Алгоритм послойный:
 *
 * 1. Слой узла — длина самого длинного пути от `start`. Так все переходы идут
 *    слева направо, а граф читается в направлении исполнения.
 * 2. Циклы ломают послойность: шаг возвращается в слой ниже себя. Такое
 *    ребро помечается как «обратное» и рисуется в обход раскладки.
 * 3. Порядок внутри слоя улучшается проходами барицентра — среднее по
 *    позициям соседей. Обмен соседей-конкурентов убирает пересечения рёбер.
 *
 * Функция не падает на кривом графе: недостижимый шаг получает слой после
 * всех достижимых, висячий переход — слой своего источника. Отрисовка
 * мусора полезнее пустого экрана.
 */

import type { StepDefinition, WorkflowDefinitionSchema } from '@/types/api'

export interface GraphNode {
  name: string
  step: StepDefinition
  /** Слой по горизонтали: целое число, больше — правее. */
  layer: number
  /** Позиция внутри слоя. */
  order: number
  x: number
  y: number
  width: number
  height: number
}

export interface GraphEdge {
  from: string
  to: string
  kind: 'next' | 'branch' | 'compensation' | 'back'
  label?: string
}

export interface GraphLayout {
  nodes: GraphNode[]
  edges: GraphEdge[]
  width: number
  height: number
}

export interface LayoutOptions {
  nodeWidth?: number
  nodeHeight?: number
  gapX?: number
  gapY?: number
  margin?: number
  /** Проходов упорядочивания по барицентру. Больше — меньше пересечений. */
  sweeps?: number
}

const DEFAULTS = {
  nodeWidth: 200,
  nodeHeight: 68,
  gapX: 90,
  gapY: 26,
  margin: 32,
  sweeps: 4,
} as const

/** Переходы шага в виде плоского списка с меткой ветки. */
function targetsOf(step: StepDefinition): Array<{ to: string; label?: string; branch: boolean }> {
  const raw = step.next
  if (raw === undefined || raw === null) return []
  if (typeof raw === 'string') return [{ to: raw, branch: false }]
  return Object.entries(raw).map(([label, to]) => ({ to, label, branch: true }))
}

/**
 * Разрывает цикл: одно ребро, ведущее в уже посещённый узел, не
 * участвует в послойной раскладке.
 */
function buildAcyclicEdges(
  steps: Record<string, StepDefinition>,
  start: string,
): { edges: GraphEdge[]; backEdges: Set<string> } {
  const edges: GraphEdge[] = []
  const backEdges = new Set<string>()
  const visiting = new Set<string>()
  const visited = new Set<string>()

  const walk = (name: string): void => {
    if (visiting.has(name)) return
    visiting.add(name)

    for (const target of targetsOf(steps[name] ?? { action: '' })) {
      if (!(target.to in steps)) continue
      if (visiting.has(target.to)) {
        // Ребро в предка замыкает цикл: слою оно противоречит, поэтому
        // помечается обратным и рисуется в обход.
        backEdges.add(`${name}->${target.to}`)
        continue
      }
      edges.push({
        from: name,
        to: target.to,
        kind: target.branch ? 'branch' : 'next',
        label: target.label,
      })
      walk(target.to)
    }

    visiting.delete(name)
    visited.add(name)
  }

  if (start in steps) walk(start)
  // Шаги вне достижимой части тоже рисуются: они есть в определении, и
  // их отсутствие на схеме выглядело бы как потеря данных.
  for (const name of Object.keys(steps)) {
    if (!visited.has(name)) walk(name)
  }

  return { edges, backEdges }
}

/** Слой каждого узла — длина самого длинного пути от стартового шага. */
function assignLayers(
  names: string[],
  edges: GraphEdge[],
  start: string,
): Map<string, number> {
  const layers = new Map<string, number>()
  const incoming = new Map<string, string[]>()
  for (const name of names) incoming.set(name, [])
  for (const edge of edges) incoming.get(edge.to)?.push(edge.from)

  // Раскладка по ширине в обход порядка: повторяем, пока не устаканится.
  // Обход в ширину даёт кратчайшие пути, а нужны самые длинные, поэтому слой
  // пересчитывается, пока значения не перестанут меняться.
  const queue = names.filter((name) => name === start || incoming.get(name)?.length === 0)
  for (const name of queue) layers.set(name, 0)

  let changed = true
  let guard = 0
  while (changed && guard < names.length + 1) {
    changed = false
    guard += 1
    for (const name of names) {
      const parents = incoming.get(name) ?? []
      if (parents.length === 0) continue
      const derived = Math.max(...parents.map((parent) => (layers.get(parent) ?? 0) + 1))
      if ((layers.get(name) ?? -1) < derived) {
        layers.set(name, derived)
        changed = true
      }
    }
  }

  for (const name of names) {
    if (!layers.has(name)) layers.set(name, 0)
  }
  return layers
}

function orderWithinLayers(nodes: GraphNode[], edges: GraphEdge[], sweeps: number): void {
  for (let sweep = 0; sweep < sweeps; sweep += 1) {
    const positions = new Map(nodes.map((node) => [node.name, node.order]))
    const barycentre = new Map<string, number>()
    const neighbours = new Map<string, string[]>()

    for (const edge of edges) {
      if (!neighbours.has(edge.to)) neighbours.set(edge.to, [])
      if (!neighbours.has(edge.from)) neighbours.set(edge.from, [])
      neighbours.get(edge.to)?.push(edge.from)
      neighbours.get(edge.from)?.push(edge.to)
    }

    for (const node of nodes) {
      const around = (neighbours.get(node.name) ?? [])
        .map((name) => positions.get(name))
        .filter((value): value is number => value !== undefined)
      barycentre.set(
        node.name,
        around.length > 0 ? around.reduce((sum, value) => sum + value, 0) / around.length : node.order,
      )
    }

    const byLayer = new Map<number, GraphNode[]>()
    for (const node of nodes) {
      const bucket = byLayer.get(node.layer) ?? []
      bucket.push(node)
      byLayer.set(node.layer, bucket)
    }

    for (const bucket of byLayer.values()) {
      bucket.sort((a, b) => (barycentre.get(a.name) ?? 0) - (barycentre.get(b.name) ?? 0))
      bucket.forEach((node, index) => {
        node.order = index
      })
    }
  }
}

/**
 * Строит раскладку графа. Циклы и недостижимые шаги не приводят к
 * бесконечному циклу: слои считаются итеративно с предельным числом проходов.
 */
export function layoutGraph(
  definition: WorkflowDefinitionSchema,
  options: LayoutOptions = {},
): GraphLayout {
  const settings = { ...DEFAULTS, ...options }
  const steps = definition.steps ?? {}
  const names = Object.keys(steps)

  if (names.length === 0) {
    return { nodes: [], edges: [], width: 0, height: 0 }
  }

  const start = names.includes(definition.start) ? definition.start : names[0]
  const { edges: forward, backEdges } = buildAcyclicEdges(steps, start)

  const edges: GraphEdge[] = [...forward]
  for (const key of backEdges) {
    const [from, to] = key.split('->')
    edges.push({ from, to, kind: 'back' })
  }

  // Компенсация рисуется от шага, а не от обработчика: обработчик — это не
  // узел графа, и стрелка к нему уводила бы в пустоту.
  for (const name of names) {
    const compensation = steps[name]?.compensation
    if (compensation) edges.push({ from: name, to: name, kind: 'compensation', label: compensation })
  }

  const layers = assignLayers(names, edges.filter((edge) => edge.kind !== 'compensation'), start)

  const nodes: GraphNode[] = names.map((name) => ({
    name,
    step: steps[name],
    layer: layers.get(name) ?? 0,
    order: 0,
    x: 0,
    y: 0,
    width: settings.nodeWidth,
    height: settings.nodeHeight,
  }))

  orderWithinLayers(nodes, forward, settings.sweeps)

  for (const node of nodes) {
    node.x = settings.margin + node.layer * (settings.nodeWidth + settings.gapX)
    node.y = settings.margin + node.order * (settings.nodeHeight + settings.gapY)
  }

  const widestLayer = nodes.reduce((max, node) => Math.max(max, node.order), 0)
  const width =
    settings.margin * 2 +
    (Math.max(...nodes.map((node) => node.layer)) + 1) * settings.nodeWidth +
    Math.max(...nodes.map((node) => node.layer)) * settings.gapX
  const height = settings.margin * 2 + (widestLayer + 1) * settings.nodeHeight + widestLayer * settings.gapY

  return { nodes, edges, width: Math.max(width, 1), height: Math.max(height, 1) }
}

/** Точки на границе узла для входа и выхода ребра. */
export function edgePoints(
  from: GraphNode,
  to: GraphNode,
): { x1: number; y1: number; x2: number; y2: number } {
  return {
    x1: from.x + from.width,
    y1: from.y + from.height / 2,
    x2: to.x,
    y2: to.y + to.height / 2,
  }
}

/** Кривая Безье между точками: стрелки выглядят аккуратнее прямых. */
export function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const bend = Math.max(30, Math.abs(x2 - x1) / 2)
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
}