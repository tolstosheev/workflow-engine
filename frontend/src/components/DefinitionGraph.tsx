/**
 * Граф схемы процесса.
 *
 * Рисуется своими средствами: граф линейный, и библиотека раскладки обошлась
 * бы дороже кода, который она заменила бы. Цвет узла — его фактическое
 * состояние в процессе, а не то, что объявлено в схеме.
 */


import { useMemo, useState } from 'react'

import { edgePath, edgePoints, layoutGraph } from '@/lib/graphLayout'
import type { GraphLayout, GraphNode } from '@/lib/graphLayout'
import { nodeStates } from '@/lib/graphState'
import type { NodeState } from '@/lib/graphState'
import type { InstanceState, StepRun, WorkflowDefinitionSchema } from '@/types/api'

const STATE_STYLES: Record<NodeState, { box: string; dot: string; label: string }> = {
  pending: { box: 'fill-surface-raised stroke-surface-border', dot: 'fill-surface-border', label: 'не начат' },
  running: { box: 'fill-accent/20 stroke-accent', dot: 'fill-accent', label: 'выполняется' },
  done: { box: 'fill-success/20 stroke-success/50', dot: 'fill-success', label: 'выполнен' },
  failed: { box: 'fill-danger/20 stroke-danger/60', dot: 'fill-danger', label: 'упал' },
  compensated: { box: 'fill-warning/20 stroke-warning/50', dot: 'fill-warning', label: 'компенсирован' },
  compensating: { box: 'fill-warning/20 stroke-warning/50', dot: 'fill-warning', label: 'компенсируется' },
}

const EDGE_STYLES: Record<string, string> = {
  next: 'stroke-white/40',
  branch: 'stroke-accent/60',
  back: 'stroke-accent/70',
  compensation: 'stroke-warning/50',
}

/**
 * Схема шагов определения с фактическим состоянием исполнения.
 *
 * Цвет узла — его состояние в инстансе. Именно это делает схему
 * показательной: видно не только «как объявлено», но и «что с этим
 * произошло» — шаг, который откатили, и шаг, который упал, видны разными
 * цветами на одном графе.
 *
 * Состояние шага выводится из прогона шага, а не из статуса инстанса:
 * `COMPENSATED` у процесса и `COMPENSATED` у конкретного шага — разные вещи.
 */
interface GraphProps {
  definition: WorkflowDefinitionSchema
  steps?: StepRun[]
  instance?: InstanceState | null
  onSelect?: (name: string) => void
}

export function DefinitionGraph({ definition, steps = [], instance = null, onSelect }: GraphProps) {
  const layout: GraphLayout = useMemo(() => layoutGraph(definition), [definition])
  const states = useMemo(() => nodeStates(steps, instance), [steps, instance])
  const [hovered, setHovered] = useState<string | null>(null)

  if (layout.nodes.length === 0) {
    return <p className="py-6 text-center text-sm text-white/50">В определении нет шагов</p>
  }

  const byName = new Map(layout.nodes.map((node) => [node.name, node]))
  const hoveredNode = hovered ? byName.get(hovered) : null

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-lg border border-surface-border bg-surface-raised p-3">
        <svg
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          className="min-w-full"
          role="img"
          aria-label="Граф шагов определения workflow"
        >
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-white/50" />
            </marker>
            <marker
              id="arrow-warning"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-warning" />
            </marker>
          </defs>

          {layout.edges.map((edge) => {
            const from = byName.get(edge.from)
            const to = byName.get(edge.to)
            if (!from || !to) return null

            if (edge.kind === 'compensation') {
              // Петля компенсации рисуется под узлом: обработчик — не узел
              // графа, и вести к нему стрелку было бы указанием в пустоту.
              return (
                <g key={`${edge.from}-compensation`}>
                  <path
                    d={`M ${from.x + 24} ${from.y + from.height} q 30 26 60 0`}
                    className={EDGE_STYLES.compensation}
                    fill="none"
                    strokeWidth={1.5}
                    strokeDasharray="3 3"
                    markerEnd="url(#arrow-warning)"
                  />
                </g>
              )
            }

            const points = edgePoints(from, to)
            const marker = edge.kind === 'next' ? 'url(#arrow)' : 'url(#arrow)'
            return (
              <g key={`${edge.from}-${edge.to}-${edge.kind}`}>
                <path
                  d={edgePath(points.x1, points.y1, points.x2, points.y2)}
                  className={EDGE_STYLES[edge.kind]}
                  fill="none"
                  strokeWidth={edge.kind === 'next' ? 1.5 : 2}
                  strokeDasharray={edge.kind === 'back' ? '5 4' : undefined}
                  markerEnd={marker}
                />
                {edge.label && (
                  <text
                    x={(points.x1 + points.x2) / 2}
                    y={(points.y1 + points.y2) / 2 - 6}
                    textAnchor="middle"
                    className="fill-white/50 text-[10px]"
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            )
          })}

          {layout.nodes.map((node) => (
            <Node
              key={node.name}
              node={node}
              state={states.get(node.name) ?? 'pending'}
              iterations={instance?.step_iterations?.[node.name] ?? 0}
              active={hovered === node.name}
              onHover={setHovered}
              onSelect={onSelect}
            />
          ))}
        </svg>
      </div>

      {hoveredNode && <NodeDetails node={hoveredNode} state={states.get(hoveredNode.name) ?? 'pending'} />}

      <Legend />
    </div>
  )
}

interface NodeProps {
  node: GraphNode
  state: NodeState
  iterations: number
  active: boolean
  onHover: (name: string | null) => void
  onSelect?: (name: string) => void
}

function Node({ node, state, iterations, active, onHover, onSelect }: NodeProps) {
  const style = STATE_STYLES[state]
  const limit = node.step.max_iterations
  const showProgress = state === 'running' && limit

  return (
    <g
      transform={`translate(${node.x} ${node.y})`}
      onMouseEnter={() => onHover(node.name)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(node.name)}
      onBlur={() => onHover(null)}
      onClick={() => onSelect?.(node.name)}
      // Узел выбирается и с клавиатуры: иначе карта шагов недоступна тому,
      // кто не держит мышь, а состояние шага — единственное, ради чего
      // карта и рисуется.
      //
      // Фокус открывает ту же подпись, что и наведение: раньше она была
      // только по мыши, и человек с клавиатуры видел голые прямоугольники без
      // действия, компенсации и правил повтора.
      role={onSelect ? 'button' : undefined}
      tabIndex={0}
      aria-label={`Шаг ${node.name}: ${style.label}`}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        onSelect?.(node.name)
      }}
      className={onSelect ? 'cursor-pointer focus:outline-none focus-visible:stroke-accent' : ''}
    >
      <rect
        width={node.width}
        height={node.height}
        rx={8}
        className={`${style.box} ${active ? 'stroke-accent' : ''}`}
        strokeWidth={active ? 2 : 1.5}
      />
      <circle cx={14} cy={node.height / 2} r={4.5} className={style.dot} />
      <text x={26} y={node.height / 2 - 2} className="fill-white text-[13px]">
        {node.name}
      </text>
      <text x={26} y={node.height / 2 + 14} className="fill-white/40 text-[10px]">
        {style.label}
        {showProgress ? ` · проход ${iterations} из ${limit}` : ''}
        {node.step.loop && limit && state !== 'running' ? ` · цикл до ${limit}` : ''}
      </text>
    </g>
  )
}

function NodeDetails({ node, state }: { node: GraphNode; state: NodeState }) {
  const step = node.step
  return (
    <div className="rounded-md border border-surface-border bg-surface-raised p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-white">{node.name}</span>
        <span className="text-white/50">{STATE_STYLES[state].label}</span>
      </div>
      {step.description && <p className="mt-1 text-white/60">{step.description}</p>}
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-white/60">
        <dt className="text-white/40">Действие</dt>
        <dd className="font-mono">{step.action}</dd>
        <dt className="text-white/40">Компенсация</dt>
        <dd className="font-mono">{step.compensation ?? '—'}</dd>
        <dt className="text-white/40">Повторы</dt>
        <dd>{step.retry ? `${step.retry.max_attempts} попыток` : 'по умолчанию'}</dd>
        <dt className="text-white/40">Ожидание</dt>
        <dd>{step.wait_before_seconds ? `${step.wait_before_seconds} с` : 'нет'}</dd>
        <dt className="text-white/40">Цикл</dt>
        <dd>{step.loop ? `до ${step.max_iterations ?? '∞'} проходов` : 'нет'}</dd>
      </dl>
    </div>
  )
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-3 text-[11px] text-white/50">
      {(Object.keys(STATE_STYLES) as NodeState[]).map((state) => (
        <span key={state} className="flex items-center gap-1.5">
          <span className={`h-2.5 w-2.5 rounded-full ${STATE_STYLES[state].dot}`} />
          {STATE_STYLES[state].label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="h-0 w-4 border-t-2 border-dashed border-accent/70" />
        обратное ребро цикла
      </span>
    </div>
  )
}