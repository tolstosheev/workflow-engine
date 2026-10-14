/**
 * Аренда на конкретном инстансе.
 *
 * Аренда вместо блокировок — одно из решений, которое с ходу не читается в
 * коде, и на странице процесса оно выглядело строкой «Аренда: worker-1».
 * Этого мало: важно не кто держит, а **до какого** — по истечении срока
 * инстанс свободен для захвата, и именно поэтому падение воркера ничего не
 * оставляет висеть.
 *
 * Счёт идёт вживую: пока человек смотрит на страницу, срок тикает, и момент
 * истечения виден своими глазами, а не написанным в документации «через TTL».
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
        Аренды нет: инстанс свободен для захвата. По истечении срока аренды это происходит само, без
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
          {expired ? 'срок истёк, инстанс свободен для захвата' : `держит ещё ${seconds(remaining!)}`}
        </span>
      )}
      {beatAge !== null && (
        <span className="text-white/30">
          {' · heartbeat '}
          {beatAge < 0 ? 'только что' : `${seconds(beatAge)} назад`}
        </span>
      )}
      {!expiresAt && <span className="text-white/40"> · срок аренды неизвестен</span>}
      <span className="block text-white/30">
        Аренда продлевается heartbeat'ом, а не удержанием блокировки: упавший воркер ничего не
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
