"""Сквозная проверка работающей системы.

Идёт по HTTP, а не к сервисам напрямую: прямой вызов пропустил бы сломанный
маршрут или неверный код ответа.

"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from scripts.definitions import DEFINITION_NAME, DEMO_INVENTORY

DEFAULT_BASE_URL = "http://localhost:8000"

# Состояния, в которых процесс уже не изменится. Ждать дальше бессмысленно:
# это либо успех, либо откат, либо требование ручного разбора.
TERMINAL_STATUSES = frozenset(
    {"COMPLETED", "COMPENSATED", "CANCELLED", "FAILED", "COMPENSATION_FAILED"}
)


@dataclass
class CheckResult:
    """Итог одной проверки."""

    name: str
    passed: bool
    detail: str = ""

    def render(self) -> str:
        mark = "OK  " if self.passed else "FAIL"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"[{mark}] {self.name}{suffix}"


@dataclass
class Scenario:
    """Контекст одного прогона."""

    client: httpx.AsyncClient
    wait_seconds: float
    checks: list[CheckResult] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append(CheckResult(name, passed, detail))
        return passed

    @property
    def failed(self) -> int:
        return sum(1 for check in self.checks if not check.passed)


async def wait_for_api(client: httpx.AsyncClient, wait_seconds: float) -> None:
    """Ждёт готовности API.

    Воркер и API поднимаются вместе с БД, и скрипт часто стартует раньше них:
    без ожидания проверка падала бы на-connect, а не на сути.
    """
    deadline = time.monotonic() + wait_seconds
    last_error = "таймаут"

    while time.monotonic() < deadline:
        try:
            response = await client.get("/health", timeout=3)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        await asyncio.sleep(0.5)

    raise SystemExit(f"API не ответил за {wait_seconds} с: {last_error}")


async def active_definition_id(client: httpx.AsyncClient) -> str | None:
    """Активное демонстрационное определение — строго по имени.

    Берётся из списка, а не создаётся: отсутствие определения означает, что не
    отработал `make seed`, и молчаливое создание замаскировало бы пустую базу.

    Выбор по имени, а не по «первому активному»: на стенде активных схем
    несколько, и проверка молча пошла бы по чужому сценарию — так и вышло с
    ожиданием доставки в час.
    """
    response = await client.get("/api/definitions", params={"limit": 200})
    response.raise_for_status()
    for item in response.json()["items"]:
        if item["is_active"] and item["name"] == DEFINITION_NAME:
            return item["id"]
    return None


async def start_instance(
    client: httpx.AsyncClient, definition_id: str, payload: dict[str, Any]
) -> str:
    response = await client.post(
        "/api/instances", json={"definition_id": definition_id, "input": payload}
    )
    response.raise_for_status()
    return response.json()["id"]


async def wait_for_terminal(
    client: httpx.AsyncClient, instance_id: str, wait_seconds: float
) -> str:
    """Дожидается терминального статуса инстанса."""
    deadline = time.monotonic() + wait_seconds
    last_status = "неизвестно"

    while time.monotonic() < deadline:
        response = await client.get(f"/api/instances/{instance_id}")
        response.raise_for_status()
        last_status = response.json()["status"]
        if last_status in TERMINAL_STATUSES:
            return last_status
        await asyncio.sleep(0.25)

    raise SystemExit(
        f"инстанс {instance_id} не достиг терминального состояния за {wait_seconds} с "
        f"(последний статус {last_status})"
    )


async def events_of(client: httpx.AsyncClient, instance_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/instances/{instance_id}/events", params={"limit": 1000})
    response.raise_for_status()
    return response.json()["items"]


async def steps_of(client: httpx.AsyncClient, instance_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/instances/{instance_id}/steps")
    response.raise_for_status()
    return response.json()["items"]


# --- Данные предметной области ---------------------------------------------
#
# Откат проверяется по данным, а не только по журналу: у отката должен быть
# предмет. Раньше обработчики ничего не писали, и «резерв снят» было не на чем.


async def _domain_snapshot() -> dict[str, Any]:
    """Резерв по складу, платежи и отправления."""
    from sqlalchemy import select

    from app.core.db import get_session_factory
    from app.models.workflow import DemoInventoryItem, DemoPayment, DemoShipment

    async with get_session_factory()() as session:
        stock = {
            row.sku: (int(row.available), int(row.reserved))
            for row in await session.scalars(select(DemoInventoryItem))
        }
        payments = sorted(
            (str(row.amount), row.status) for row in await session.scalars(select(DemoPayment))
        )
        shipments = sorted(row.status for row in await session.scalars(select(DemoShipment)))
    return {"stock": stock, "payments": payments, "shipments": shipments}


def _reserved_for(snapshot: dict[str, Any], sku: str) -> int:
    return snapshot["stock"].get(sku, (0, -1))[1]


async def _available_for(sku: str) -> int:
    """Свободный остаток по артикулу — чтобы заказ заведомо превышал склад."""
    return (await _domain_snapshot())["stock"].get(sku, (0, 0))[0]


# Артикулы берутся из склада, который наполняет `make seed`: несуществующий
# артикул упал бы с «товар не найден на складе» и не сказал бы ничего.
DEMO_SKU = DEMO_INVENTORY[0]["sku"]
FAIL_SKU = DEMO_INVENTORY[1]["sku"]


async def check_happy_path(scenario: Scenario, definition_id: str) -> None:
    """Полный проход без сбоев."""
    # Снимок до запуска: проверяется рост данных, а не абсолютное значение —
    # платежи и отправления от предыдущих прогонов в базе остаются.
    before = await _domain_snapshot()
    payments_before = len(before["payments"])
    shipments_before = len(before["shipments"])

    instance_id = await start_instance(
        scenario.client,
        definition_id,
        {
            "order_number": "SMOKE-OK",
            "items": [{"sku": DEMO_SKU, "qty": 2}],
            "amount": "199.00",
        },
    )

    status = await wait_for_terminal(scenario.client, instance_id, scenario.wait_seconds)
    scenario.record(
        "успешный проход завершается в COMPLETED",
        status == "COMPLETED",
        f"статус {status}",
    )

    events = await events_of(scenario.client, instance_id)
    types = [event["type"] for event in events]

    scenario.record(
        "журнал начинается с INSTANCE_CREATED",
        bool(types) and types[0] == "INSTANCE_CREATED",
        f"первое событие {types[0] if types else 'нет'}",
    )
    # Монотонность seq — следствие индекса на (instance_id, seq); проверяется
    # на данных, потому что именно сбой записиseq незаметен по статусам.
    seqs = [event["seq"] for event in events]
    scenario.record(
        "seq событий строго монотонен",
        seqs == sorted(seqs) and len(set(seqs)) == len(seqs),
        f"{len(seqs)} событий",
    )

    steps = await steps_of(scenario.client, instance_id)
    completed = [step for step in steps if step["status"] == "COMPLETED"]
    scenario.record(
        "все шаги отмечены выполненными",
        len(completed) == len(steps) and bool(steps),
        f"{len(completed)}/{len(steps)}",
    )
    scenario.record(
        "у каждого шага записаны тайминги",
        all(step["finished_at"] is not None for step in steps),
    )

    state = await scenario.client.get(f"/api/instances/{instance_id}/state")
    state.raise_for_status()
    replayed = state.json()
    scenario.record(
        "состояние восстанавливается replay'ом журнала",
        replayed["is_terminal"] is True and replayed["status"] == "COMPLETED",
        f"replay: {replayed['status']}",
    )

    # Данные после полного прохода. Проверяется изменение относительно
    # снимка, а не абсолютное значение: склад и платежи могли быть заняты
    # другими прогонами.
    domain = await _domain_snapshot()
    # Доставка отпускает резерв (ADR-0020), поэтому после успешного заказа
    # склад возвращается к исходному состоянию. Проверка «резерв вырос» была
    # бы проверкой противоположного свойства — что товар остался в холде
    # навсегда, и склад пустеет от каждого заказа.
    scenario.record(
        "резерв возвращается на склад после доставки",
        _reserved_for(domain, DEMO_SKU) == _reserved_for(before, DEMO_SKU),
        f"резерв {_reserved_for(domain, DEMO_SKU)}, до заказа было "
        f"{_reserved_for(before, DEMO_SKU)}",
    )
    scenario.record(
        "списание и отправление записаны в данные",
        len(domain["payments"]) > payments_before and len(domain["shipments"]) > shipments_before,
        f"платежей {len(domain['payments'])} (было {payments_before}), "
        f"отправлений {len(domain['shipments'])} (было {shipments_before})",
    )


async def check_compensation(scenario: Scenario, definition_id: str) -> None:
    """Контрольный пример: сбой на оплате и автоматический откат."""
    # Снимок снимается до запуска: резерв сравнивается с тем, что было до
    # заказа, а не с абсолютным нулём — склад может быть занят другими.
    before = await _domain_snapshot()
    reserved_before = _reserved_for(before, FAIL_SKU)
    payments_before = len(before["payments"])
    shipments_before = len(before["shipments"])

    instance_id = await start_instance(
        scenario.client,
        definition_id,
        {"order_number": "SMOKE-FAIL", "items": [{"sku": FAIL_SKU, "qty": 1}], "amount": "99.00"},
    )

    # Инъекция адресная: иначе сломались бы параллельные прогоны.
    injection = await scenario.client.post(
        "/api/chaos/fail-step",
        json={
            "instance_id": instance_id,
            "step": "charge_payment",
            "mode": "always",
            "error": "сбой шага (инъекция)",
        },
    )
    injection.raise_for_status()

    status = await wait_for_terminal(scenario.client, instance_id, scenario.wait_seconds * 2)
    scenario.record(
        "сбой шага приводит к откату, а не к зависанию",
        status == "COMPENSATED",
        f"статус {status}",
    )

    steps = await steps_of(scenario.client, instance_id)
    by_name = {step["step_name"]: step for step in steps}
    scenario.record(
        "упавший шаг не считается выполненным",
        by_name.get("charge_payment", {}).get("status") == "FAILED",
        f"статус {by_name.get('charge_payment', {}).get('status', 'нет шага')}",
    )
    # Резерв предшествует оплате, поэтому именно он обязан быть откатан:
    # если бы откат шёл вперёд, деньги вернулись бы, а товар остался бы зажат.
    scenario.record(
        "выполненный до сбоя шаг откатан",
        by_name.get("reserve_inventory", {}).get("compensated") is True,
    )

    events = await events_of(scenario.client, instance_id)
    types = [event["type"] for event in events]
    scenario.record(
        "журнал содержит начало и завершение отката",
        "COMPENSATION_STARTED" in types and "COMPENSATION_COMPLETED" in types,
    )
    scenario.record(
        "завершение отката записано в журнал",
        types[-1] == "INSTANCE_COMPENSATED",
        f"последнее событие {types[-1] if types else 'нет'}",
    )

    # Главная проверка сценария: откат возвращает данные, а не только
    # дописывает события в журнал. Снимок снят до запуска сломанного заказа,
    # поэтому сравнение идёт с ним, а не с абсолютным нулём: склад может быть
    # занят другими прогонами.
    after = await _domain_snapshot()
    scenario.record(
        "откат вернул резерв на складе к исходному",
        _reserved_for(after, FAIL_SKU) == reserved_before,
        f"было {reserved_before}, стало {_reserved_for(after, FAIL_SKU)}",
    )
    # Проверяются только записи, созданные этим заказом: платежи от других
    # прогонов в базе остаются и не должны мешать.
    new_payments = [status for _, status in after["payments"][payments_before:]]
    scenario.record(
        "откат не оставил несписанных средств",
        "HELD" not in new_payments,
        f"новые платежи {new_payments}",
    )
    new_shipments = after["shipments"][shipments_before:]
    scenario.record(
        "откат не оставил активных отправлений",
        "CREATED" not in new_shipments,
        f"новые отправления {new_shipments}",
    )


async def check_refund_after_charge(scenario: Scenario, definition_id: str) -> None:
    """Сбой после списания: деньги приходится возвращать по-настоящему.

    Сбой на оплате не проверяет возврат средств — платёж там просто не
    создаётся. Чтобы компенсация возврата была доказана данными, сбой
    ставится на шаг после списания.
    """
    before = await _domain_snapshot()
    payments_before = len(before["payments"])

    instance_id = await start_instance(
        scenario.client,
        definition_id,
        {"order_number": "SMOKE-REFUND", "items": [{"sku": FAIL_SKU, "qty": 1}], "amount": "77.00"},
    )
    injection = await scenario.client.post(
        "/api/chaos/fail-step",
        json={
            "instance_id": instance_id,
            "step": "create_shipment",
            "mode": "always",
            "error": "сбой шага (инъекция)",
        },
    )
    injection.raise_for_status()

    status = await wait_for_terminal(scenario.client, instance_id, scenario.wait_seconds * 2)
    await scenario.client.delete("/api/chaos")

    scenario.record(
        "сбой после списания приводит к откату",
        status == "COMPENSATED",
        f"статус {status}",
    )

    after = await _domain_snapshot()
    new_payments = [state for _, state in after["payments"][payments_before:]]
    scenario.record(
        "откат вернул списанные средства",
        bool(new_payments) and all(state == "REFUNDED" for state in new_payments),
        f"новые платежи {new_payments}",
    )


async def check_beyond_stock(scenario: Scenario, definition_id: str) -> None:
    """Заказ больше остатка: сбой настоящий, без подставной неисправности.

    Резерв ограничен `reserved <= available`, поэтому заказ падает на шаге
    резервирования и уходит в компенсацию сам.

    Резерв сравнивается со снимком «до», а не с нулём: подтверждение доставки
    склад не трогает (ADR-0006), резерв накапливается у каждого заказа, и
    проверка на ноль объявляла бы утечкой чужой резерв.
    """
    reserved_before = _reserved_for(await _domain_snapshot(), FAIL_SKU)
    available = await _available_for(FAIL_SKU)
    beyond = available + 10_000

    instance_id = await start_instance(
        scenario.client,
        definition_id,
        {
            "order_number": "SMOKE-BEYOND",
            "items": [{"sku": FAIL_SKU, "qty": beyond}],
            "amount": "5.00",
        },
    )
    status = await wait_for_terminal(scenario.client, instance_id, scenario.wait_seconds * 2)

    # CANCELLED здесь законен: шаг резервирования не выполнился, компенсировать
    # нечего, и называть это COMPENSATED было бы враньём.
    scenario.record(
        "заказ больше остатка падает, а не проходит",
        status in {"COMPENSATED", "FAILED", "CANCELLED"},
        f"статус {status}, запрошено {beyond} при остатке {available}",
    )

    by_name = {step["step_name"]: step for step in await steps_of(scenario.client, instance_id)}
    scenario.record(
        "нехватка товара остановила шаг резервирования",
        by_name.get("reserve_inventory", {}).get("status") == "FAILED",
        f"статус {by_name.get('reserve_inventory', {}).get('status', 'нет шага')}",
    )

    snapshot = await _domain_snapshot()
    scenario.record(
        "недоступный товар не оставил резерва",
        _reserved_for(snapshot, FAIL_SKU) == reserved_before,
        f"резерв {_reserved_for(snapshot, FAIL_SKU)}, до заказа {reserved_before}",
    )


async def run(base_url: str, wait_seconds: float, compensation_only: bool) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        await wait_for_api(client, wait_seconds)

        definition_id = await active_definition_id(client)
        if definition_id is None:
            print(
                "нет активных определений — выполните `make seed` перед smoke-прогоном",
                file=sys.stderr,
            )
            return 2

        scenario = Scenario(client=client, wait_seconds=wait_seconds)

        # Один инстанс готовится заранее: иначе длительность отката
        # включала бы и работу первых трёх шагов, которые к откату отношения
        # не имеют — компенсируется только то, что уже выполнено.
        warmup_id = await start_instance(
            client,
            definition_id,
            {
                "order_number": "SMOKE-WARMUP",
                "items": [{"sku": DEMO_SKU, "qty": 1}],
                "amount": "10.00",
            },
        )
        await wait_for_terminal(client, warmup_id, wait_seconds)

        started = time.monotonic()
        await check_compensation(scenario, definition_id)
        fail_elapsed = time.monotonic() - started

        happy_elapsed = 0.0
        if not compensation_only:
            started = time.monotonic()
            await check_happy_path(scenario, definition_id)
            happy_elapsed = time.monotonic() - started
            # Сбой после списания: единственный сценарий, где возврат средств
            # действительно происходит, а не «платёж не создался».
            await check_refund_after_charge(scenario, definition_id)
            # Сбой без инъекции: заказ больше остатка. Идёт последним, чтобы
            # проверка не влияла на предыдущие, где резерв ожидался занятым.
            await check_beyond_stock(scenario, definition_id)

        scenario.record(
            "откат укладывается в разумное время",
            fail_elapsed < 60,
            f"{fail_elapsed:.1f} с",
        )

        await client.delete("/api/chaos")

        print(f"API: {base_url}")
        print(f"определение: {definition_id}")
        if compensation_only:
            print(f"откат после сбоя: {fail_elapsed:.1f} с\n")
        else:
            print(f"успешный проход: {happy_elapsed:.1f} с, с откатом: {fail_elapsed:.1f} с\n")
        for check in scenario.checks:
            print(check.render())

        total = len(scenario.checks)
        print(f"\n{total - scenario.failed}/{total} проверок пройдено")
        return 1 if scenario.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Сквозная проверка работающей системы")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="базовый адрес API")
    parser.add_argument(
        "--wait", type=float, default=30, help="предел ожидания одного инстанса, секунд"
    )
    parser.add_argument(
        "--compensation-only",
        action="store_true",
        help="только контрольный пример с откатом, без проверки полного прохода",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.wait, args.compensation_only))


if __name__ == "__main__":
    raise SystemExit(main())
