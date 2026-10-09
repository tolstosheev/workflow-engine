"""Сквозная проверка работающей системы.

Скрипт обращается к API по HTTP, а не к сервисам напрямую: проверять движок
надо тем же путём, каким им пользуется клиент. Прямой вызов сервисного слоя
пропустил бы, например, сломанный маршрут или неверный код ответа.

Сценарий по умолчанию — контрольный пример курсовой: сбой на шаге оплаты и
автоматический откат выполненных шагов в обратном порядке.

Код возврата ненулевой при любом расхождении: скрипт предназначен для
использования в CI и в `make smoke`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

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
    """Активное демонстрационное определение.

    Берётся из списка, а не создаётся здесь: если определения нет, сначала
    должен отработать `make seed` — молча создавать его в проверке означало бы
    замаскировать пустую базу.
    """
    response = await client.get("/api/definitions", params={"limit": 200})
    response.raise_for_status()
    for item in response.json()["items"]:
        if item["is_active"] and item["name"] != "":
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


async def check_happy_path(scenario: Scenario, definition_id: str) -> None:
    """Полный проход без сбоев."""
    instance_id = await start_instance(
        scenario.client,
        definition_id,
        {"order_number": "SMOKE-OK", "items": [{"sku": "SKU-000", "qty": 2}], "amount": "199.00"},
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


async def check_compensation(scenario: Scenario, definition_id: str) -> None:
    """Контрольный пример: сбой на оплате и автоматический откат."""
    instance_id = await start_instance(
        scenario.client,
        definition_id,
        {"order_number": "SMOKE-FAIL", "items": [{"sku": "SKU-001", "qty": 1}], "amount": "99.00"},
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
                "items": [{"sku": "SKU-000", "qty": 1}],
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
