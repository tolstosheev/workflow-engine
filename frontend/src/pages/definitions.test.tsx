// @vitest-environment jsdom
/**
 * Редактор определений: сохранение, проверка и запуск.
 *
 * Страница отвечает за путь от текста до работающего процесса, и каждый её
 * шаг может тихо сорваться:
 *
 * - определение с тем же именем и версией создать нельзя, и без предложения
 *   обновить каждый, кто сохранил образец дважды, упирался в 409;
 * - проверка обязана отличать ошибку от предупреждения: ошибка — процесс не
 *   запустится, предупреждение — компенсируется не полностью, и путать их хуже,
 *   чем не показывать ничего;
 * - сброс к образцу затирает набранный текст, и спрашивать об этом нужно до;
 * - образец обязан оставаться пригодным: он назван иначе, чем то, что создаёт
 *   наполнение, и сохраняться должен при любом состоянии базы.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DefinitionsPage from '@/pages/definitions'

const list = vi.fn()
const create = vi.fn()
const update = vi.fn()
const activate = vi.fn()
const validate = vi.fn()
const validatePayload = vi.fn()
const start = vi.fn()

vi.mock('@/api/client', () => ({
  definitions: {
    list: (...args: unknown[]) => list(...args),
    create: (...args: unknown[]) => create(...args),
    update: (...args: unknown[]) => update(...args),
    activate: (...args: unknown[]) => activate(...args),
    validate: (...args: unknown[]) => validate(...args),
    validatePayload: (...args: unknown[]) => validatePayload(...args),
  },
  instances: { start: (...args: unknown[]) => start(...args) },
}))

const EXISTING = {
  id: 'd-old',
  name: 'demo_order',
  version: 1,
  is_active: true,
  created_at: '2026-03-01T09:00:00Z',
  updated_at: '2026-03-01T09:00:00Z',
}

/**
 * Открывает страницу и дожидается, пока список сохранённых определений придёт.
 *
 * Ждать заголовка недостаточно: решение «обновлять или создавать» страница
 * принимает по загруженному списку, и нажатие до прихода ответа сохраняло бы
 * определение без вопроса — ровно тот случай, который проверяют тесты ниже.
 */
async function open(): Promise<void> {
  await mount()
  await screen.findByRole('table', { name: 'Определения workflow' })
}

/**
 * Кнопка в строке таблицы, а не первая такая же на странице.
 *
 * «Проверить» есть и у редактора, и у каждой строки; без области поиска
 * нашлась бы первая, и проверялось бы не то определение.
 */
function rowButton(name: string): HTMLElement {
  const table = screen.getByRole('table', { name: 'Определения workflow' })
  return within(table).getByRole('button', { name })
}

/** То же для случая, когда сохранённых определений нет и таблицы не будет. */
async function openEmpty(): Promise<void> {
  await mount()
  await screen.findByText('Определений пока нет')
}

async function mount(): Promise<void> {
  render(
    <MemoryRouter>
      <DefinitionsPage />
    </MemoryRouter>,
  )
  await screen.findByRole('heading', { name: 'Определения workflow' })
}

beforeEach(() => {
  for (const spy of [list, create, update, activate, validate, validatePayload, start]) {
    spy.mockReset()
  }
  list.mockResolvedValue({ items: [EXISTING], total: 1 })
  create.mockResolvedValue({ id: 'd-new', name: 'demo_order', version: 1 })
  update.mockResolvedValue(EXISTING)
  activate.mockResolvedValue(EXISTING)
  validate.mockResolvedValue({ valid: true, errors: [], warnings: [] })
  validatePayload.mockResolvedValue({ valid: true, errors: [], warnings: [] })
  start.mockResolvedValue({ id: 'i-1' })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

/**
 * Ждёт, пока действие дойдёт до вопроса.
 *
 * Страница сохраняет «в пустоту» и не ждёт ответа, поэтому проверка успевала
 * пройти раньше обработчика. `waitFor` ждёт по умолчанию секунду, чего хватает
 * на пустой машине и не хватает при двадцати трёх файлах сразу: срок задан явно,
 * потому что лимит должен описывать машину разработчика, а не её загрузку.
 */
async function waitForQuestion(assertion: () => void): Promise<void> {
  await waitFor(assertion, { timeout: 5_000 })
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('редактор определений', () => {
  it('начинает с определения, которое можно сохранить', () => {
    // Образец назван иначе, чем то, что создаёт наполнение базы: с тем же
    // именем и версией сохранение падало бы в 409 при любом состоянии стенда.
    render(
      <MemoryRouter>
        <DefinitionsPage />
      </MemoryRouter>,
    )

    const editor = screen.getByRole('textbox', { name: /определение/i }) as HTMLTextAreaElement

    expect(JSON.parse(editor.value).name).toBe('demo_order')
  })

  it('спрашивает, обновлять ли уже существующее определение', async () => {
    await open()

    await fireEvent.click(screen.getByRole('button', { name: 'Сохранить и активировать' }))

    await waitForQuestion(() => expect(window.confirm).toHaveBeenCalled())
    expect(update).toHaveBeenCalledOnce()
    expect(create).not.toHaveBeenCalled()
  })

  it('ничего не сохраняет, если отказ', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await open()

    await fireEvent.click(screen.getByRole('button', { name: 'Сохранить и активировать' }))

    // Вопрос задаётся и при отказе — ждать надо его, а не результата: при отказе
    // результата нет, и проверка «ничего не сохранено» прошла бы мгновенно,
    // ничего не дождавшись.
    await waitForQuestion(() => expect(window.confirm).toHaveBeenCalled())
    expect(update).not.toHaveBeenCalled()
    expect(create).not.toHaveBeenCalled()
  })

  it('передаёт в обновление только схему, без имени и версии', async () => {
    await open()

    await fireEvent.click(screen.getByRole('button', { name: 'Сохранить и активировать' }))

    await waitForQuestion(() => expect(update).toHaveBeenCalledOnce())

    // Обновление принимает только саму схему: имя и версия в нём запрещены,
    // и запрос с ними падал бы в 422.
    expect(update.mock.calls[0][1]).toEqual({
      definition: expect.anything(),
      is_active: true,
    })
  })

  it('показывает, что определение сохранено', async () => {
    await open()

    await fireEvent.click(screen.getByRole('button', { name: 'Сохранить и активировать' }))

    // `findByText` ждёт сам: пауза перед ним лишь добавила бы гонку.
    expect(await screen.findByText(/сохранено и активировано/)).toBeTruthy()
  })

  it('показывает причину, если сохранить не удалось', async () => {
    create.mockRejectedValue(new Error('определение уже существует'))
    list.mockResolvedValue({ items: [], total: 0 })
    await openEmpty()

    await fireEvent.click(screen.getByRole('button', { name: 'Сохранить и активировать' }))

    // Молчаливая неудача хуже: человек решил бы, что сохранил, и запустил бы
    // не то, что собирался.
    expect(await screen.findByText('определение уже существует')).toBeTruthy()
  })

  it('объясняет по-русски, что входные данные не читаются', async () => {
    await open()

    // Поле входа принимает свободный текст, и вставленный обрывок — обычное
    // дело. Сообщение движка JavaScript на русской странице объясняет ничего:
    // человек не знает, что «Unexpected token» — это про `{`, а не про сервер.
    await fireEvent.change(screen.getByLabelText('Входные данные процесса в формате JSON'), {
      target: { value: '{ не json' },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Запустить' }))

    expect(await screen.findByText(/JSON не разбирается/)).toBeTruthy()
    expect(start).not.toHaveBeenCalled()
  })

  it('объясняет по-русски, что определение не читается', async () => {
    await open()

    await fireEvent.change(screen.getByLabelText('Определение workflow в формате JSON'), {
      target: { value: '{ сломанный' },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Сохранить и активировать' }))

    expect(await screen.findByText(/JSON не разбирается/)).toBeTruthy()
    expect(create).not.toHaveBeenCalled()
  })

  it('запускает выбранное определение с введёнными данными', async () => {
    await open()

    await fireEvent.change(screen.getByLabelText('Входные данные процесса в формате JSON'), {
      target: { value: '{"sku":"SKU-1","qty":2}' },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Выбрать' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Запустить выбранное определение' }))

    // Входные данные уходят в запрос как есть: процесс получает ровно то, что
    // человек ввёл, и редактор не подставляет ничего от себя.
    expect(start).toHaveBeenCalledWith('d-old', { sku: 'SKU-1', qty: 2 })
  })

  it('проверяет сохранённое определение по его строке', async () => {
    await open()

    await fireEvent.click(rowButton('Проверить'))

    // У каждого определения своя проверка: проверять всё подряд значило бы
    // спрашивать сервер обо всех строках при нажатии одной кнопки.
    expect(validate).toHaveBeenCalledWith('d-old')
  })

  it('объясняет, если проверка не прошла', async () => {
    validate.mockRejectedValue(new Error('определение недоступно'))
    await open()

    await fireEvent.click(rowButton('Проверить'))

    expect(await screen.findByText('определение недоступно')).toBeTruthy()
  })

  it('разделяет ошибки и предупреждения', async () => {
    validatePayload.mockResolvedValue({
      valid: false,
      errors: [{ code: 'unknown_action', step: 'charge', message: 'обработчик не найден' }],
      warnings: [{ code: 'no_compensation', step: 'ship', message: 'компенсация не назначена' }],
    })
    await open()

    await fireEvent.click(screen.getAllByRole('button', { name: 'Проверить' })[0])

    expect(await screen.findByText('Ошибок: 1')).toBeTruthy()
    expect(screen.getByText('charge: обработчик не найден')).toBeTruthy()
    expect(screen.getByText('ship: компенсация не назначена')).toBeTruthy()
  })

  it('спрашивает перед сбросом набранного текста', async () => {
    await open()

    const editor = screen.getByRole('textbox', { name: /определение/i })
    await fireEvent.change(editor, { target: { value: '{"name": "черновик"}' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Сбросить к образцу' }))

    expect(window.confirm).toHaveBeenCalled()
  })

  it('не теряет текст, если сброс отменён', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await open()

    const editor = screen.getByRole('textbox', { name: /определение/i }) as HTMLTextAreaElement
    await fireEvent.change(editor, { target: { value: '{"name": "черновик"}' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Сбросить к образцу' }))

    // Сброс без вопроса затирал бы набранное молча, и работа терялась бы.
    expect(editor.value).toBe('{"name": "черновик"}')
  })

  it('предупреждает о несохранённых изменениях', async () => {
    await open()

    expect(screen.queryByText(/несохранённые изменения/)).toBeNull()

    await fireEvent.change(screen.getByRole('textbox', { name: /определение/i }), {
      target: { value: '{"name": "черновик"}' },
    })

    expect(screen.getByText(/несохранённые изменения/)).toBeTruthy()
  })

  it('держит входные данные под рукой до выбора', async () => {
    await open()

    // Форма всегда на месте: она принадлежит редактору, а не выбору, и
    // появляться по клику означало бы лишний шаг перед каждым запуском.
    expect(screen.getByLabelText('Входные данные процесса в формате JSON')).toBeTruthy()
  })

  it('открывает запуск после выбора определения', async () => {
    await open()

    await fireEvent.click(screen.getByRole('button', { name: 'Выбрать' }))

    expect(screen.getByLabelText('Входные данные процесса в формате JSON')).toBeTruthy()
  })

  it('показывает, какое определение выбрано', async () => {
    await open()

    const choose = screen.getByRole('button', { name: 'Выбрать' })
    expect(choose.getAttribute('aria-pressed')).toBe('false')

    await fireEvent.click(choose)

    // Выбранное определение было видно только по цвету кнопки: с клавиатуры и
    // для скринридера непонятно, к какому из них относится карточка запуска.
    expect(screen.getByRole('button', { name: 'Выбрать' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('запускает процесс у выбранного определения', async () => {
    await open()

    await fireEvent.click(screen.getByRole('button', { name: 'Выбрать' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Запустить' }))

    expect(start).toHaveBeenCalledOnce()
  })
})