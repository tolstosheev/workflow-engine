/**
 * Проверка «состояние = разбор журнала».
 *
 * Состояние выводится повтором журнала (§3.1 `AGENTS.md`), и утверждение это
 * фундаментальное, но из интерфейса его нельзя было ни проверить, ни увидеть.
 * Кнопка перечитывает состояние из журнала и сравнивает с колонкой: расхождение
 * означает изменение в обход журнала, и его называют, а не прячут.
 *
 * Исключение одно: у процесса с убранным журналом (ADR-0019) сравнивать
 * нечего, и там объявляется уборка.
 */


import { useState } from 'react'

import { Button, ErrorState } from '@/components/ui'
import { instances as instancesApi } from '@/api/client'
import { statusLabel } from '@/lib/status'
import type { InstanceStatus } from '@/types/api'

interface ReplayCheckProps {
  instanceId: string
  /** Состояние из колонки — то, чему журнал должен подтверждать или противоречить. */
  status: InstanceStatus
  currentStep: string | null
  /** Момент уборки журнала; для такого процесса проверка невозможна. */
  journalPrunedAt?: string | null
}

type Verdict =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'error'; message: string }
  | {
      kind: 'done'
      replayed: { status: InstanceStatus; current_step: string | null }
      stepDiffers: boolean
    }

export function ReplayCheck({
  instanceId,
  status,
  currentStep,
  journalPrunedAt,
}: ReplayCheckProps) {
  const [verdict, setVerdict] = useState<Verdict>({ kind: 'idle' })
  const pruned = Boolean(journalPrunedAt)

  const check = async () => {
    setVerdict({ kind: 'checking' })
    try {
      const replayed = await instancesApi.state(instanceId)
      setVerdict({
        kind: 'done',
        replayed: { status: replayed.status, current_step: replayed.current_step ?? null },
        // Шаг в колонке опережает шаг в разборе на текущей итерации цикла, и
        // расхождение по шагу — не повод объявлять дефект: несовпадение статуса
        // уже означало бы нарушение, а шаг живёт своей жизнью.
        stepDiffers: (replayed.current_step ?? null) !== currentStep,
      })
    } catch (cause) {
      setVerdict({
        kind: 'error',
        message: cause instanceof Error ? cause.message : 'Не удалось перечитать состояние',
      })
    }
  }

  if (pruned) {
    return (
      <div className="mt-3 border-t border-surface-border pt-3 text-xs">
        <p className="text-white/60">
          Журнал этого процесса убран уборкой старых архивных записей (ADR-0019),
          поэтому сверить состояние с разбором нечем: событий нет. Колонка процесса
          осталась последним известным значением.
        </p>
      </div>
    )
  }

  return (
    <div className="mt-3 border-t border-surface-border pt-3">
      <Button disabled={verdict.kind === 'checking'} onClick={() => void check()}>
        {verdict.kind === 'checking' ? 'Проверяем…' : 'Проверить: состояние = журнал'}
      </Button>

      {verdict.kind === 'error' && (
        <div className="mt-2">
          <ErrorState message={verdict.message} />
        </div>
      )}

      {verdict.kind === 'done' && (
        <VerdictTable verdict={verdict} column={{ status, current_step: currentStep }} />
      )}
    </div>
  )
}

function VerdictTable({
  verdict,
  column,
}: {
  verdict: Extract<Verdict, { kind: 'done' }>
  column: { status: InstanceStatus; current_step: string | null }
}) {
  const sameStatus = verdict.replayed.status === column.status

  return (
    <div className="mt-2 text-xs">
      <p className={sameStatus ? 'text-success' : 'text-danger'}>
        {sameStatus
          ? 'Совпадает: колонка подтверждена журналом, состояние восстанавливается'
          : 'Расхождение: состояние изменено в обход журнала — это дефект'}
      </p>
      <dl className="mt-2 space-y-1">
        <Row label="В колонке" value={statusLabel(column.status)} />
        <Row label="Из журнала" value={statusLabel(verdict.replayed.status)} />
        {verdict.stepDiffers && (
          <p className="text-white/40">
            Шаг в колонке «{column.current_step ?? '—'}», в разборе «{verdict.replayed.current_step ?? '—'}»:
            на текущей итерации цикла колонка стоит на шаг впереди, и это не расхождение состояния.
          </p>
        )}
      </dl>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-28 shrink-0 text-white/40">{label}</dt>
      <dd className="text-white/70">{value}</dd>
    </div>
  )
}
