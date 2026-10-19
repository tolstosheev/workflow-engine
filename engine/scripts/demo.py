"""Три акта показа: прогон по ручкам страницы «Сценарии», реплики и замер.

Драйвер делает то же, что делает `/scenarios`, только по HTTP: снятие правил,
выбор определения по имени, инъекции, запуск, ожидание и свидетельства из
журнала. Новых ручек показ не требует, поэтому страница и драйвер не могут
разойтись в механике; разойтись они могут в тексте, и сверку текста даёт
`tests/unit/test_demo_acts.py`.

Замер идёт от запуска процесса до сверки свидетельств — ровно то число,
которое потом печатается в `docs/OPERATIONS.md`: берётся оно из прогона здесь,
а не из памяти.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from scripts.definitions import DEFINITION_NAME, HANDLERS, order_input
from scripts.smoke import (
    TERMINAL_STATUSES,
    CheckResult,
    default_base_url,
    wait_for_api,
)

DEFAULT_BASE_URL = default_base_url()

POLL_SECONDS = 0.25

#: Обработчик возврата резерва — единственный, что ломается в третьем акте.
RELEASE_HANDLER = f"{HANDLERS}:release_inventory"


class ActError(Exception):
    """Акт не дошёл до нужного состояния.

    Отдельный тип нужен, чтобы отказ одного акта не ронял остальные: система
    жива, а не доехал ровно этот прогон — показывать дальше всё равно можно.
    """


@dataclass(frozen=True, slots=True)
class Evidence:
    """Свидетельство акта: событие в журнале, без которого акт не доказан.

    Состояния мало — «Компенсирован» бывает и у процесса, у которого изменения
    не вернулись, поэтому подтверждение читается из событий, как на странице.
    """

    label: str
    event: str
    at_least: int = 1
    # Отбор по payload: "step" у выполнения шага, "for_step" у компенсации.
    payload_field: str | None = None
    payload_value: str | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    """Правило инъекции — то же тело, что шлёт страница «Сценарии»."""

    kind: Literal["step", "compensation"]
    target: str
    mode: str = "always"
    error: str = "сбой шага (сценарий)"


@dataclass(frozen=True, slots=True)
class Act:
    """Один акт показа: что сказать, что запустить и что обязано быть видно."""

    number: int
    title: str
    cue: str
    point_at: str
    wait_for: str
    evidence: tuple[Evidence, ...] = ()
    rules: tuple[Rule, ...] = ()
    # Третий акт: после retry_from причина снимается и компенсация
    # повторяется вручную, итог — retry_to.
    retry_from: str | None = None
    retry_to: str | None = None


ACTS: tuple[Act, ...] = (
    Act(
        number=1,
        title="Заказ проходит целиком",
        cue=(
            "Граф проходит без вмешательства: шаги живут миллисекунды, "
            "а всё остальное — ожидания между ними."
        ),
        point_at=(
            "Лента времени с пробелами между шагами и панель «Склад» "
            "с нулевым резервом после доставки."
        ),
        wait_for="COMPLETED",
        evidence=(
            Evidence(
                label="подтверждение доставки выполнено",
                event="STEP_COMPLETED",
                payload_field="step",
                payload_value="await_delivery",
            ),
        ),
    ),
    Act(
        number=2,
        title="Платёж отклонён, заказ компенсируется",
        cue=(
            "Сбой платежа возвращает уже выполненные шаги в обратном порядке — "
            "это и есть компенсация."
        ),
        point_at=(
            "Журнал: COMPENSATION_STARTED и завершённый возврат резерва; склад снова без резерва."
        ),
        wait_for="COMPENSATED",
        rules=(Rule(kind="step", target="charge_payment"),),
        evidence=(
            Evidence(
                label="резерв на складе освобождён",
                event="COMPENSATION_COMPLETED",
                payload_field="for_step",
                payload_value="reserve_inventory",
            ),
            Evidence(label="компенсация начата", event="COMPENSATION_STARTED"),
        ),
    ),
    Act(
        number=3,
        title="Компенсация не выполняется",
        cue=(
            "Провал возврата не прячется: процесс встаёт в «Компенсация не выполнена», "
            "после чего причину снимают и компенсируют повторно вручную."
        ),
        point_at=(
            "Вкладка «Откат не удался» в списке; после повтора — «Утверждение "
            "подтверждено» и подпись о повторе компенсации."
        ),
        wait_for="COMPENSATION_FAILED",
        rules=(
            Rule(kind="step", target="create_shipment"),
            Rule(kind="compensation", target=RELEASE_HANDLER),
        ),
        retry_from="COMPENSATION_FAILED",
        retry_to="COMPENSATED",
        evidence=(
            Evidence(
                label="резерв освобождён",
                event="COMPENSATION_COMPLETED",
                payload_field="for_step",
                payload_value="reserve_inventory",
            ),
            Evidence(
                label="компенсация повторена после первой неудачной попытки",
                event="COMPENSATION_STARTED",
                at_least=2,
                payload_field="for_step",
                payload_value="reserve_inventory",
            ),
            Evidence(label="компенсация завершена без ошибок", event="COMPENSATION_COMPLETED"),
        ),
    ),
)


def check_evidence(
    evidence: tuple[Evidence, ...], events: list[dict[str, Any]]
) -> list[CheckResult]:
    """Сверяет свидетельства с журналом — чистая функция, проверяется на данных."""
    results: list[CheckResult] = []
    for item in evidence:
        found = [
            event
            for event in events
            if event.get("type") == item.event
            and (
                item.payload_field is None
                or event.get("payload", {}).get(item.payload_field) == item.payload_value
            )
        ]
        passed = len(found) >= item.at_least
        detail = f"событий {item.event}: {len(found)}"
        if not passed:
            detail += f", ожидалось не меньше {item.at_least}"
        results.append(CheckResult(name=item.label, passed=passed, detail=detail))
    return results


def rule_payload(rule: Rule) -> dict[str, Any]:
    """Тело запроса правила — как его строит страница.

    Текст ошибки относится к шагу и не отправляется вместе с обработчиком
    компенсации: страница тоже его не шлёт, и расхождение в теле стало бы
    скрытой разницей между двумя путями одного показа.
    """
    if rule.kind == "step":
        return {"step": rule.target, "mode": rule.mode, "error": rule.error}
    return {"handler": rule.target, "mode": rule.mode}


async def clear_rules(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/chaos")
    response.raise_for_status()


async def clear_rules_quiet(client: httpx.AsyncClient) -> str:
    """Снятие правил, не роняющее акт: ошибка возвращается, а не проглатывается.

    Проглоченная здесь ошибка была бы неотличима от успеха, а оставшееся
    правило сломало бы следующий акт — сообщить обязан каждый путь.
    """
    try:
        await clear_rules(client)
    except httpx.HTTPError as exc:
        return f"правила инъекции не снялись: {exc}"
    return ""


async def resolve_definition(client: httpx.AsyncClient) -> str:
    """Определение показа — по имени и включённое в любом случае.

    По имени, а не «первое активное»: активных на стенде несколько, и молча
    взятое первое означало бы чужой сценарий. Выключенное включается — уборка
    однажды выключала определение, и следующий прогон падал на нём же.
    """
    response = await client.get("/api/definitions", params={"limit": 200})
    response.raise_for_status()
    for item in response.json()["items"]:
        if item["name"] != DEFINITION_NAME:
            continue
        if item["is_active"]:
            return str(item["id"])
        activated = await client.post(f"/api/definitions/{item['id']}/activate")
        activated.raise_for_status()
        return str(activated.json()["id"])
    raise SystemExit(f"нет определения {DEFINITION_NAME}: запустите python -m scripts.seed")


async def apply_rules(client: httpx.AsyncClient, rules: tuple[Rule, ...]) -> None:
    for rule in rules:
        path = "/api/chaos/fail-step" if rule.kind == "step" else "/api/chaos/fail-compensation"
        response = await client.post(path, json=rule_payload(rule))
        response.raise_for_status()


async def start_instance(client: httpx.AsyncClient, definition_id: str) -> str:
    response = await client.post(
        "/api/instances", json={"definition_id": definition_id, "input": order_input()}
    )
    response.raise_for_status()
    return str(response.json()["id"])


async def wait_for(
    client: httpx.AsyncClient, instance_id: str, target: str, wait_seconds: float
) -> str:
    """Ждёт целевое состояние; чужой терминальный статус — немедленный отказ.

    После чужого терминального состояния ждать бессмысленно: процесс уже не
    изменится, и ожидание превратило бы ясный отказ в паузу в весь таймаут.
    """
    deadline = time.monotonic() + wait_seconds
    last = "неизвестно"
    while time.monotonic() < deadline:
        response = await client.get(f"/api/instances/{instance_id}")
        response.raise_for_status()
        last = str(response.json()["status"])
        if last == target:
            return last
        if last in TERMINAL_STATUSES:
            raise ActError(f"процесс пришёл в {last} вместо {target}")
        await asyncio.sleep(POLL_SECONDS)
    raise ActError(f"за {wait_seconds:.0f} с последнее состояние {last}, ожидалось {target}")


async def events_of(client: httpx.AsyncClient, instance_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/instances/{instance_id}/events", params={"limit": 1000})
    response.raise_for_status()
    return list(response.json()["items"])


@dataclass
class ActResult:
    """Итог одного акта — то, что печатается и что считается успехом."""

    act: Act
    instance_id: str
    status: str
    elapsed: float
    checks: list[CheckResult] = field(default_factory=list)
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(check.passed for check in self.checks)


async def run_act(client: httpx.AsyncClient, act: Act, wait_seconds: float) -> ActResult:
    """Один акт: чистые правила, запуск, ожидание, свидетельства.

    Правила снимаются до запуска и после акта в любом исходе: правило на
    стенде одно на всех, и оставшееся сломало бы следующий акт — страница по
    той же причине снимает их в конце прогона.
    """
    result = ActResult(act=act, instance_id="", status="", elapsed=0.0)
    started = 0.0
    try:
        await clear_rules(client)
        definition_id = await resolve_definition(client)
        await apply_rules(client, act.rules)
        started = time.monotonic()
        result.instance_id = await start_instance(client, definition_id)
        result.status = await wait_for(client, result.instance_id, act.wait_for, wait_seconds)
        if act.retry_from is not None and result.status == act.retry_from:
            # Причина снимается до повтора: с оставшейся поломкой повтор упал бы
            # снова, и выглядело бы так, что кнопка не работает. Успевает
            # ручной: автоматический повтор ждёт паузу не меньше пяти секунд.
            await clear_rules(client)
            retry = await client.post(f"/api/instances/{result.instance_id}/compensation/retry")
            retry.raise_for_status()
            result.status = await wait_for(
                client, result.instance_id, act.retry_to or act.wait_for, wait_seconds
            )
        events = await events_of(client, result.instance_id)
        result.checks = check_evidence(act.evidence, events)
    except (httpx.HTTPError, ActError) as exc:
        result.error = str(exc)
    if started:
        result.elapsed = time.monotonic() - started
    cleanup = await clear_rules_quiet(client)
    if cleanup and not result.error:
        result.error = cleanup
    return result


def render(result: ActResult) -> list[str]:
    """Строки вывода одного акта: реплика, указание и проверки."""
    act = result.act
    lines = [
        f"Акт {act.number} — «{act.title}»",
        f"  Сказать: {act.cue}",
        f"  Смотреть: {act.point_at}",
    ]
    if result.instance_id:
        lines.append(f"  Процесс: {result.instance_id}")
    for check in result.checks:
        lines.append(f"  {check.render()}")
    if result.error:
        lines.append(f"  [FAIL] {result.error}")
    if result.elapsed:
        lines.append(f"  От запуска до подтверждения: {result.elapsed:.1f} с")
    return lines


async def run(base_url: str, only: int | None, wait_seconds: float) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        await wait_for_api(client, wait_seconds=30)

        acts = [act for act in ACTS if only is None or act.number == only]
        # Акты идут подряд, а не вместе: правило инъекции одно на всех, и
        # параллельный акт снял бы поломку у соседа — страница держит кнопки
        # заблокированными по той же причине.
        results = [await run_act(client, act, wait_seconds) for act in acts]

        print(f"API: {base_url}")
        for result in results:
            print()
            for line in render(result):
                print(line)

        passed = sum(1 for result in results if result.passed)
        total = sum(result.elapsed for result in results)
        print()
        print(f"{passed}/{len(results)} актов подтверждено, всего {total:.1f} с")
        return 0 if passed == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Три акта показа на защите")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="базовый адрес API")
    parser.add_argument("--act", type=int, choices=(1, 2, 3), help="только один акт")
    parser.add_argument(
        "--wait", type=float, default=180, help="предел ожидания одного состояния, секунд"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.act, args.wait))


if __name__ == "__main__":
    raise SystemExit(main())
