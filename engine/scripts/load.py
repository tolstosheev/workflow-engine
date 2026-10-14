"""Нагрузочный прогон и замеры.

Что и зачем измеряется — в `docs/BENCHMARKS.md`, здесь только механика.

Три величины, и каждая отвечает на свой вопрос:

- **throughput** — сколько инстансов в секунду доводит система до конца.
  Показывает, сколько транзакций движок держит на единицу времени.
- **latency по шагам** — сколько занимает каждый шаг. Разброс между шагами
  виден здесь и не виден в throughput: пять шагов по 20 мс дают то же
  суммарное время, что и один шаг на 100 мс.
- **время компенсации** — сколько занимает откат после сбоя. Это цена
  долгой транзакции: чем больше шагов успело выполниться, тем дороже откат.

Инстансы создаются по HTTP тем же путём, что и в бою: прямая вставка в БД
измерила бы скорость `INSERT`, а не движка.

Запуск: `python -m scripts.load --instances 200`
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from scripts.definitions import DEFINITION_NAME, order_input
from scripts.smoke import TERMINAL_STATUSES, wait_for_api

DEFAULT_BASE_URL = "http://localhost:8000"

# Создание инстансов идёт пачками: по одному запросу на инстанс замер бы
# измерял задержку HTTP, а не движка.
DEFAULT_CONCURRENCY = 10

# Компенсация замеряется на сбое платёжного шага: к этому моменту выполнены
# резерв и валидация, то есть откатывать есть что.
COMPENSATION_STEP = "charge_payment"


@dataclass
class StepLatency:
    """Длительности шагов, собранные из `step_runs`."""

    samples: dict[str, list[float]] = field(default_factory=dict)

    def add(self, step_name: str, seconds: float) -> None:
        self.samples.setdefault(step_name, []).append(seconds)

    def merge(self, other: StepLatency) -> None:
        for step_name, values in other.samples.items():
            self.samples.setdefault(step_name, []).extend(values)

    def rows(self) -> list[tuple[str, int, float, float, float, float]]:
        """Шаг, число замеров, среднее, медиана, p95, максимум."""
        result = []
        for step_name, values in sorted(self.samples.items()):
            ordered = sorted(values)
            p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
            result.append(
                (
                    step_name,
                    len(ordered),
                    statistics.fmean(ordered),
                    statistics.median(ordered),
                    ordered[p95_index],
                    ordered[-1],
                )
            )
        return result


def _body(response: httpx.Response | BaseException) -> dict[str, Any] | None:
    """Тело успешного ответа или `None`.

    Отдельная функция нужна ради сужения типа: `asyncio.gather` с
    `return_exceptions=True` возвращает смесь, и mypy не сужает её после
    `isinstance` прямо в цикле.
    """
    if isinstance(response, BaseException):
        return None
    if response.status_code != 200:
        return None
    return response.json()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


async def _definition_id(client: httpx.AsyncClient) -> str:
    """Определение замера — строго по имени, а не «первое активное».

    Выбор по активности молча менял предмет замера: стенд может держать больше
    одного активного определения (сквозные проверки создают своё), и замерял
    бы то, что попалось первым. Такой прогон печатает уверенный throughput и
    ничего не говорит о том, что мерил не то — выглядит как результат.
    """
    response = await client.get("/api/definitions", params={"limit": 200})
    response.raise_for_status()
    for item in response.json()["items"]:
        if item["name"] == DEFINITION_NAME and item["is_active"]:
            return item["id"]
    raise SystemExit(
        f"нет активного определения {DEFINITION_NAME!r} — выполните `make seed` перед замером"
    )


async def _create_batch(
    client: httpx.AsyncClient,
    definition_id: str,
    start: int,
    count: int,
    break_step: str | None = None,
) -> tuple[list[str], list[str]]:
    """Создаёт инстансы, ограничивая число одновременных запросов.

    `break_step` — шаг, который нужно сломать сразу после создания. Инъекция
    идёт в том же запросе потока, а не отдельным проходом после пачки: иначе
    воркер успевает исполнить платёжный шаг раньше, чем дойдёт инъекция, и
    измеренный «откат» окажется обычным успешным проходом.

    Возвращает созданные инстансы и те, где инъекция не успела.
    """
    semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)
    missed: list[str] = []

    async def create(index: int) -> str | None:
        payload = {
            "definition_id": definition_id,
            "input": order_input(index),
        }
        async with semaphore:
            try:
                response = await client.post("/api/instances", json=payload)
                response.raise_for_status()
                instance_id = response.json()["id"]
            except httpx.HTTPError as exc:
                print(f"  инстанс {index} не создан: {exc}", file=sys.stderr)
                return None

            if break_step is not None:
                await client.post(
                    "/api/chaos/fail-step",
                    json={
                        "instance_id": instance_id,
                        "step": break_step,
                        "mode": "always",
                        "error": "сбой шага (замер компенсации)",
                    },
                )
            return instance_id

    results = await asyncio.gather(*(create(start + i) for i in range(count)))
    created = [instance_id for instance_id in results if instance_id is not None]

    if break_step is not None:
        for instance_id in created:
            status = await _status_of(client, instance_id)
            if status == "COMPLETED":
                missed.append(instance_id)

    return created, missed


async def _status_of(client: httpx.AsyncClient, instance_id: str) -> str | None:
    response = await client.get(f"/api/instances/{instance_id}")
    body = _body(response)
    return None if body is None else body["status"]


async def _wait_all(
    client: httpx.AsyncClient, instance_ids: list[str], wait_seconds: float
) -> dict[str, str]:
    """Ждёт терминального состояния всех инстансов и возвращает их статусы."""
    statuses: dict[str, str] = {}
    deadline = time.monotonic() + wait_seconds

    pending = set(instance_ids)
    while pending and time.monotonic() < deadline:
        batch = list(pending)[:200]
        responses = await asyncio.gather(
            *(client.get(f"/api/instances/{i}") for i in batch), return_exceptions=True
        )
        for instance_id, response in zip(batch, responses, strict=True):
            body = _body(response)
            if body is None:
                continue
            status = body["status"]
            if status in TERMINAL_STATUSES:
                statuses[instance_id] = status
                pending.discard(instance_id)
        if pending:
            await asyncio.sleep(0.25)

    return statuses


async def _collect_latency(client: httpx.AsyncClient, instance_ids: list[str]) -> StepLatency:
    """Собирает длительности шагов через API.

    Источник — `step_runs`, а не замеры со стороны клиента: клиент видит
    суммарное время процесса, а движок уже знает, где именно вышло.
    """
    latency = StepLatency()

    for start in range(0, len(instance_ids), 50):
        batch = instance_ids[start : start + 50]
        responses = await asyncio.gather(
            *(client.get(f"/api/instances/{i}/steps") for i in batch),
            return_exceptions=True,
        )
        for response in responses:
            body = _body(response)
            if body is None:
                continue
            for step in body["items"]:
                if step["finished_at"] is None:
                    continue
                started = datetime.fromisoformat(step["started_at"])
                finished = datetime.fromisoformat(step["finished_at"])
                latency.add(step["step_name"], (finished - started).total_seconds())

    return latency


async def measure_throughput(
    client: httpx.AsyncClient, definition_id: str, instances: int, wait_seconds: float
) -> tuple[StepLatency, float, int]:
    """Полный проход N инстансов. Возвращает задержки, throughput и успехи."""
    # Первый замер — холодный: поднимаются соединения и заполняются кэши
    # дефинишена. В отчёт он не попадает, иначе цифра зависела бы от того,
    # был ли стенд до этого прогнан.
    await _create_batch(client, definition_id, 0, 1)
    await _wait_all(client, [], wait_seconds)

    started = time.monotonic()
    instance_ids, _ = await _create_batch(client, definition_id, 1, instances)
    created_at = time.monotonic()
    statuses = await _wait_all(client, instance_ids, wait_seconds)
    elapsed = time.monotonic() - started

    completed = sum(1 for status in statuses.values() if status == "COMPLETED")
    latency = await _collect_latency(client, instance_ids)

    print(
        f"\nсоздано {len(instance_ids)} за {created_at - started:.2f} с, "
        f"завершено {completed} из них"
    )
    print(f"полный проход: {elapsed:.2f} с, throughput {completed / elapsed:.1f} инстансов/с")

    return latency, completed / elapsed, completed


async def measure_compensation(
    client: httpx.AsyncClient, definition_id: str, instances: int, wait_seconds: float
) -> list[float]:
    """Откат после сбоя платёжного шага. Возвращает длительности откатов."""
    print("\nзамер компенсации: сбой на шаге оплаты")

    # Инъекция уходит сразу после создания инстанса, а не отдельным проходом:
    # иначе воркер успевает дойти до платёжного шага раньше инъекции.
    instance_ids, raced = await _create_batch(
        client, definition_id, 5000, instances, break_step=COMPENSATION_STEP
    )
    if raced:
        print(f"  {len(raced)} инстансов прошло платёжный шаг раньше инъекции — в замер не войдут")

    started = time.monotonic()
    await _wait_all(client, instance_ids, wait_seconds * 3)
    elapsed = time.monotonic() - started

    compensated = 0
    durations: list[float] = []
    for instance_id in instance_ids:
        if instance_id in raced:
            continue
        status = await _status_of(client, instance_id)
        if status != "COMPENSATED":
            print(f"  {instance_id}: статус {status} — не откатан")
            continue
        compensated += 1

        events = await client.get(f"/api/instances/{instance_id}/events", params={"limit": 1000})
        body = _body(events)
        if body is None:
            continue

        # Откат измеряется от первого COMPENSATION_STARTED до завершения:
        # это и есть цена, которую платит бизнес за откат уже выполненных
        # шагов. Время самих шагов к ней отношения не имеет.
        started_at = None
        finished_at = None
        for event in body["items"]:
            if event["type"] == "COMPENSATION_STARTED" and started_at is None:
                started_at = event["created_at"]
            if event["type"] == "INSTANCE_COMPENSATED":
                finished_at = event["created_at"]
        if started_at and finished_at:
            durations.append(
                (
                    datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)
                ).total_seconds()
            )

    await client.delete("/api/chaos")

    measured = len(instance_ids) - len(raced)
    print(f"откатов завершено: {compensated} из {measured} за {elapsed:.2f} с")
    return durations


def render_steps(latency: StepLatency) -> None:
    print("\nдлительность шагов (секунды)")
    print(f"{'шаг':<20} {'замеров':>8} {'среднее':>10} {'медиана':>10} {'p95':>10} {'макс':>10}")
    for step_name, count, mean, median, p95, maximum in latency.rows():
        print(
            f"{step_name:<20} {count:>8} {mean:>10.4f} {median:>10.4f} "
            f"{p95:>10.4f} {maximum:>10.4f}"
        )


def render_compensation(durations: list[float]) -> None:
    if not durations:
        print("\nкомпенсации не измерены")
        return
    print(f"\nвремя компенсации (секунды), замеров: {len(durations)}")
    print(
        f"  среднее {statistics.fmean(durations):.3f}  "
        f"медиана {statistics.median(durations):.3f}  "
        f"p95 {percentile(durations, 0.95):.3f}  "
        f"макс {max(durations):.3f}"
    )


async def clean_state(definition_id: str) -> int:
    """Удаляет инстансы и их журналы, оставляя определения.

    Без очистки замер зависит от истории стенда: незавершённые инстансы
    предыдущего прогона остаются в очереди, и воркер сначала дожимает их —
    тогда «throughput» измеряет не систему, а объём накопленного мусора.

    Инстансы удаляются каскадно по внешним ключам: таблица `workflow_events`
    ссылается на инстанс, и удаление инстанса без журнала нарушило бы
    единственный источник истины.
    """
    from sqlalchemy import text

    from app.core.db import dispose_engine, session_scope

    async def truncate() -> int:
        async with session_scope() as session:
            removed = await session.scalar(
                text("SELECT count(*) FROM workflow_instances WHERE definition_id = :id"),
                {"id": definition_id},
            )
            await session.execute(
                text(
                    "TRUNCATE TABLE compensation_log, step_runs, timers, "
                    "processed_operations, workflow_events, demo_payments, demo_shipments, "
                    "workflow_instances RESTART IDENTITY CASCADE"
                )
            )
            return int(removed or 0)

    try:
        return await truncate()
    finally:
        await dispose_engine()


async def run(base_url: str, instances: int, wait_seconds: float, purge: bool) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        await wait_for_api(client, wait_seconds)
        definition_id = await _definition_id(client)

        print(f"API: {base_url}")
        print(f"определение: {definition_id}")
        print(f"инстансов в прогоне: {instances}")

        if purge:
            removed = await clean_state(definition_id)
            print(f"очищено инстансов: {removed}")

        latency, throughput, completed = await measure_throughput(
            client, definition_id, instances, wait_seconds
        )
        render_steps(latency)

        durations = await measure_compensation(
            client, definition_id, max(10, instances // 10), wait_seconds
        )
        render_compensation(durations)

        total_latency = [value for values in latency.samples.values() for value in values]
        print("\nсводка")
        print(f"  throughput: {throughput:.1f} инстансов/с")
        print(f"  шагов выполнено: {len(total_latency)}")
        print(f"  завершено успешно: {completed} из {instances}")

        # Провал замеров — не ошибка движка, но повод не публиковать цифры.
        if completed == 0:
            print("\nни один инстанс не завершился: замеры недостоверны", file=sys.stderr)
            return 1
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Нагрузочный прогон и замеры")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="базовый адрес API")
    parser.add_argument("--instances", type=int, default=200, help="сколько инстансов запустить")
    parser.add_argument("--wait", type=float, default=120, help="предел ожидания, секунд")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="не очищать базу перед прогоном (замер зависит от истории стенда)",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.instances, args.wait, purge=not args.keep))


if __name__ == "__main__":
    raise SystemExit(main())
