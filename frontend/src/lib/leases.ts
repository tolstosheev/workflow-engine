/**
 * Кто какие процессы держит.
 *
 * Числа исполнителей здесь нет: исполнитель без процессов не оставляет
 * следов, и по этому списку видно только тех, кто что-то держит.
 */


import type { WorkflowInstance } from '@/types/api'

export interface WorkerLease {
  owner: string
  instances: WorkflowInstance[]
  lastHeartbeat: string | null
}

export function collectLeases(instances: WorkflowInstance[]): WorkerLease[] {
  const byOwner = new Map<string, WorkflowInstance[]>()

  for (const instance of instances) {
    const owner = instance.lease_owner
    if (!owner) continue
    const bucket = byOwner.get(owner) ?? []
    bucket.push(instance)
    byOwner.set(owner, bucket)
  }

  return [...byOwner.entries()]
    .map(([owner, owned]) => ({
      owner,
      instances: owned,
      lastHeartbeat:
        owned
          .map((item) => item.heartbeat_at)
          .filter((value): value is string => value !== null)
          .sort()
          .pop() ?? null,
    }))
    .sort((a, b) => a.owner.localeCompare(b.owner))
}