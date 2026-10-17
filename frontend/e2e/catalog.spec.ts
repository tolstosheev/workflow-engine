import { expect, test, type APIRequestContext } from '@playwright/test'

import { fetchWithRetry } from './http'

/**
 * Каталог сценариев целиком, подряд, в одной сессии браузера.
 *
 * Отдельные проверки того же каталога каждая начинают с чистого правила
 * инъекции и потому ничего не говорят о зависимости сценариев друг от друга.
 * А зависимость тут была заложена в саму конструкцию: правило снимается
 * глобально, и снятие одного прогона прилетает следующему, если тот уже начат.
 *
 * Поэтому здесь сценарии идут в порядке каталога, без пауз на уборку между
 * ними: следующий стартует сразу, как только предыдущий показал итог, а его
 * опрос может ещё работать. Итог каждого — «Утверждение подтверждено
 * состоянием и данными журнала»; одно исключение оговорено отдельно.
 */

const API = process.env.E2E_API_URL ?? 'http://localhost:8000'

const PROVEN = 'Утверждение подтверждено состоянием и данными журнала'
const OBSERVED = 'и ждёт решения'

interface Expectation {
  title: string
  /** Ожидаемый итог на карточке. */
  outcome: 'proven' | 'observed'
}

const CATALOG: Expectation[] = [
  { title: 'Заказ проходит целиком', outcome: 'proven' },
  { title: 'Платёж отказывает, заказ откатывается', outcome: 'proven' },
  { title: 'Откат не проходит', outcome: 'proven' },
  { title: 'Повтор отката не возвращает средства дважды', outcome: 'proven' },
  { title: 'Повтор шага спасает процесс', outcome: 'proven' },
  { title: 'Товара не хватает', outcome: 'proven' },
  { title: 'Отмена процесса', outcome: 'proven' },
  { title: 'Долгое ожидание доставки', outcome: 'proven' },
  { title: 'Ворота согласования', outcome: 'proven' },
  // Пауза со сроком обязана дойти до конца: сценарий заявляет, что по истечении
  // срока процесс отменяется и резерв освобождается, и остановиться на самой
  // паузе — значит не проверить ни то, ни другое.
  { title: 'Срок ожидания истёк', outcome: 'proven' },
  { title: 'Цикл исчерпан', outcome: 'proven' },
  { title: 'Процесс переживает остановку продления аренды', outcome: 'proven' },
]

async function clearRules(request: APIRequestContext): Promise<void> {
  await fetchWithRetry(request, `${API}/api/chaos`, 'delete')
}

test('все сценарии каталога проходят подряд, не завися друг от друга', async ({
  page,
  request,
}) => {
  // Двенадцать сценариев, из них ожидание доставки, ожидание человека, срок
  // ожидания и истечение аренды: стандартный срок проверки не годится.
  test.setTimeout(20 * 60 * 1000)

  await page.goto('/scenarios')

  const failures: string[] = []

  for (const expectation of CATALOG) {
    await clearRules(request)
    const card = page
      .getByRole('heading', { name: expectation.title, exact: true })
      .locator('xpath=ancestor::section[1]')

    await card.getByRole('button', { name: 'Запустить' }).click()

    const wanted = expectation.outcome === 'proven' ? PROVEN : OBSERVED
    try {
      await expect(card.getByText(wanted)).toBeVisible({ timeout: 150_000 })
    } catch {
      const seen = await card.innerText()
      failures.push(`${expectation.title}: ${seen.replace(/\s+/g, ' ').slice(0, 200)}`)
      continue
    }
    // Утверждение, помеченное словом «подтверждено», обязано иметь зелёные
    // свидетельства: строка с «✗» — это подтверждение, которого не было.
    if (expectation.outcome === 'proven') {
      const failed = await card.getByText('✗').count()
      if (failed > 0) failures.push(`${expectation.title}: свидетельства не подтвердились`)
    }
    // Следующий сценарий стартует сразу: так проходит настоящий показ, где
    // предыдущий прогон ещё доживает в фоне.
  }

  expect(failures, `не прошли:\n${failures.join('\n')}`).toEqual([])
})

test('пока идёт сценарий, второй не запускается и первый не теряет поломку', async ({
  page,
}) => {
  // Дефект, найденный на живом стенде: кнопка блокировалась только на время
  // запуска, поэтому второй сценарий начинался поверх идущего и снимал его
  // правило общим снятием. Первый процесс проходил «корректно» — то есть
  // показывал прямо противоположное своему названию, и это выглядело как
  // исправная работа движка.
  test.setTimeout(5 * 60 * 1000)

  await page.goto('/scenarios')

  const first = page
    .getByRole('heading', { name: 'Платёж отказывает, заказ откатывается', exact: true })
    .locator('xpath=ancestor::section[1]')
  const second = page
    .getByRole('heading', { name: 'Откат не проходит', exact: true })
    .locator('xpath=ancestor::section[1]')

  await first.getByRole('button', { name: 'Запустить' }).click()

  // Второй не просто заблокирован — сказано почему, иначе кнопка выглядит
  // сломанной.
  await expect(second.getByRole('button', { name: 'Запустить' })).toBeDisabled()
  await expect(page.getByText(/Идёт сценарий/)).toBeVisible()

  // Первый при этом доходит до своего состояния: его правило никто не снял.
  await expect(first.getByText(PROVEN)).toBeVisible({ timeout: 150_000 })
  await expect(first.getByText('✗')).toHaveCount(0)

  // После итога кнопки оживают, и каталог не встал.
  await expect(second.getByRole('button', { name: 'Запустить' })).toBeEnabled()
})