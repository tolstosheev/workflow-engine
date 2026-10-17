/**
 * Закрепление процесса на конкретном исполнителе.
 *
 * Важно не кто держит, а **до какого**: по истечении срока процесс свободен,
 * и именно поэтому падение исполнителя ничего не оставляет висеть. Срок
 * тикает при человеке — момент истечения виден, а не написан в документации.
 */


import { useEffect, useState } from 'react'

interface LeaseDetailsProps {
  owner: string | null
  expiresAt: string | null
  heartbeatAt: string | null
}

export function LeaseDetails({ owner, expiresAt, heartbeatAt }: LeaseDetailsProps) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!expiresAt) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [expiresAt])

  if (!owner) {
    return (
      <p className="mt-2 text-xs text-white/40">
        Закрепления нет: процесс свободен для захвата. По истечении срока закрепления это
        снятия блокировок вручную.
      </p>
    )
  }

  const expires = expiresAt ? new Date(expiresAt).getTime() : null
  const remaining = expires === null ? null : expires - now
  const expired = remaining !== null && remaining <= 0
  const beat = heartbeatAt ? new Date(heartbeatAt).getTime() : null
  const beatAge = beat === null ? null : now - beat

  return (
    <p className="mt-2 text-xs">
      <span className="font-mono text-white/70">{owner}</span>
      {expires !== null && (
        <span className={expired ? 'text-danger' : 'text-white/50'}>
          {' · '}
          {expired ? 'срок истёк, процесс свободен' : `держит ещё ${seconds(remaining!)}`}
        </span>
      )}
      {beatAge !== null && (
        <span className="text-white/30">
          {' · активность ' }
          {beatAge < 0 ? 'только что' : `${seconds(beatAge)} назад`}
        </span>
      )}
      {!expiresAt && <span className="text-white/40"> · срок закрепления неизвестен</span>}
      <span className="block text-white/30">
        Закрепление продлевается отметкой активности, а не удержанием блокировки: упавший
        оставляет висеть.
      </span>
    </p>
  )
}

function seconds(millis: number): string {
  const value = Math.round(millis / 1000)
  if (value < 60) return `${value} с`
  return `${Math.floor(value / 60)} мин ${value % 60} с`
}
