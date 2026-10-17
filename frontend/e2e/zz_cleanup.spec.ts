import { expect, test } from '@playwright/test'

/**
 * Сквозные проверки не имеют права оставлять на стенде свои процессы.
 *
 * Три процесса из `stand.spec.ts` уходили в паузу и оставались там навсегда: на
 * защите в списке висят три заказа под именем `e2e_manual_gate`, которых никто не
 * заказывал, и панель склада показывает их резерв — удержание без объяснения.
 * Проверка, которая проходит и оставляет мусор, проверяет не то, зачем написана.
 *
 * Файл называется `zz_`, чтобы идти последним: уборка живёт в `afterAll` соседнего
 * файла и без порядка эта проверка увидела бы состояние до неё.
 *
 * Ограничение намеренное: пауза человека на стенде — законное состояние (ADR-0017
 * прячет незакрытое только из архива), поэтому проверка требует отсутствия пауз лишь
 * у схем, чьё имя начинается с `e2e_`.
 */
test('проверки не оставили на стенде процессов в паузе', async ({ request }) => {
  const api = process.env.E2E_API_URL ?? 'http://localhost:8000'

  const listed = await request.get(`${api}/api/instances?limit=200&archived=all`)
  expect(listed.status()).toBeLessThan(400)
  const body = (await listed.json()) as {
    items: Array<{ id: string; status: string; definition: { name: string } }>
  }

  const leftovers = body.items.filter(
    (item) => item.status === 'PAUSED' && item.definition.name.startsWith('e2e_'),
  )

  expect(
    leftovers.map((item) => `${item.definition.name}:${item.id}`),
    'схемы проверок не должны оставаться в паузе: их резерв висит на складе',
  ).toEqual([])
})