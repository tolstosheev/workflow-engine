/**
 * Проверка «состояние = разбор журнала».
 *
 * Состояние процесса не хранится полем — оно выводится повтором журнала
 * (§3.1 AGENTS.md). Утверждение это фундаментальное, но из интерфейса его
 * нельзя было ни проверить, ни увидеть: страница просто показывала и то, и
 * другое рядом.
 *
 * Кнопка делает проверку при человеке. Она перечитывает состояние из журнала
 * и сравнивает с тем, что лежит в колонке инстанса. Совпадение означает, что
 * колонка догнала журнал и восстановление после падения возможно; расхождение
 * означает, что кто-то изменил состояние в обход журнала, и это уже дефект,
 * который надо назвать, а не спрятать.
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

export function ReplayCheck({ instanceId, status, currentStep }: ReplayCheckProps) {
  const [verdict, setVerdict] = useState<Verdict>({ kind: 'idle' })

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
