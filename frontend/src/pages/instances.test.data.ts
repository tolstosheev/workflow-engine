/**
 * Данные для тестов списка инстансов.
 *
 * Живут отдельным модулем, а не внутри теста: мок клиента поднимается до
 * импорта теста и не видит переменных, объявленных ниже. Три процесса
 * различаются именем, шагом и временем создания, чтобы поиск и сортировку
 * можно было отличить друг от друга.
 */

import type { WorkflowInstance } from '@/types/api'

function instance(
  id: string,
  name: string,
  step: string,
  created: string,
): WorkflowInstance {
  return {
    id,
    definition: { id: 'd1', name, version: 1 },
    status: 'RUNNING',
    current_step: step,
    variables: {},
    lease_owner: null,
    lease_expires_at: null,
    heartbeat_at: null,
    archived_at: null,
    created_at: created,
    updated_at: created,
  } as unknown as WorkflowInstance
}

const INSTANCES = [
  instance('i-old', 'order-old', 'charge', '2026-01-01T10:00:00Z'),
  instance('i-mid', 'order-mid', 'reserve', '2026-02-01T10:00:00Z'),
  instance('i-new', 'order-new', 'ship', '2026-03-01T10:00:00Z'),
]

/** Новый массив на каждый вызов: стор и ответ сервера не должны им делиться. */
export function storeFixture(): WorkflowInstance[] {
  return INSTANCES.map((row) => ({ ...row }))
}