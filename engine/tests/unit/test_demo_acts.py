"""Три акта показа: свидетельства, порядок действий и сверка с документом.

Драйвер и страница «Сценарии» ходят по одним ручкам, но текст у них свой:
реплики лежат и в скрипте, и в `docs/OPERATIONS.md`, а расхождение между ними
не вылезает ни на экран, ни в журнал — его обязан ловить здесь проверка.

Делаются акты через `httpx.MockTransport`: проверяется порядок обращений, а не
сеть, и нужной сети в тесте нет.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from scripts.definitions import DEFINITION_NAME
from scripts.demo import ACTS, Evidence, check_evidence, rule_payload, run_act

ROOT = Path(__file__).resolve().parents[3]
OPERATIONS = ROOT / "docs" / "OPERATIONS.md"


def _event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {"type": event_type, "payload": payload, "seq": 1}


def test_completion_for_the_right_step_confirms_the_act() -> None:
    events = [
        _event("COMPENSATION_STARTED", for_step="reserve_inventory"),
        _event("COMPENSATION_COMPLETED", for_step="reserve_inventory"),
    ]
    evidence = (
        Evidence(
            label="резерв освобождён",
            event="COMPENSATION_COMPLETED",
            payload_field="for_step",
            payload_value="reserve_inventory",
        ),
    )

    (result,) = check_evidence(evidence, events)

    assert result.passed
    assert "COMPENSATION_COMPLETED" in result.detail


def test_completion_of_another_step_proves_nothing() -> None:
    events = [
        _event("COMPENSATION_COMPLETED", for_step="charge_payment"),
        _event("COMPENSATION_FAILED", for_step="reserve_inventory"),
    ]
    evidence = (
        Evidence(
            label="резерв освобождён",
            event="COMPENSATION_COMPLETED",
            payload_field="for_step",
            payload_value="reserve_inventory",
        ),
    )

    (result,) = check_evidence(evidence, events)

    assert not result.passed
    assert "ожидалось не меньше 1" in result.detail


def test_repeated_compensation_needs_two_starts_for_the_step() -> None:
    evidence = (
        Evidence(
            label="компенсация повторена после первой неудачной попытки",
            event="COMPENSATION_STARTED",
            at_least=2,
            payload_field="for_step",
            payload_value="reserve_inventory",
        ),
    )
    once = [_event("COMPENSATION_STARTED", for_step="reserve_inventory")]
    twice = once + [_event("COMPENSATION_STARTED", for_step="reserve_inventory")]

    assert not check_evidence(evidence, once)[0].passed
    assert check_evidence(evidence, twice)[0].passed


def test_each_act_is_written_into_the_show_notes() -> None:
    """Реплика живёт в двух местах, и расходится оно молча.

    Документ пишется после скрипта, и при следующей правке реплики в скрипте
    чтение на защите осталось бы со старой её версией.
    """
    text = OPERATIONS.read_text(encoding="utf-8")

    for act in ACTS:
        assert act.title in text, f"название акта {act.number} не найдено в OPERATIONS.md"
        assert act.cue in text, f"реплика акта {act.number} не доехала до OPERATIONS.md"
        assert act.point_at in text, f"указание акта {act.number} не доехало до OPERATIONS.md"


def test_compensation_rule_carries_no_error_text() -> None:
    """Обработчик отката получает только ссылку и режим — как на странице."""
    step, compensation = ACTS[2].rules

    assert "error" in rule_payload(step)
    assert "error" not in rule_payload(compensation)


def test_reason_is_cleared_before_the_manual_repeat() -> None:
    """Повтор идёт уже без поломки, и правила сняты после акта в любом исходе.

    Правило осталось бы после акта — следующий сломался бы чужой причиной,
    а порядок «сначала снять» проверяется здесь на последовательности
    обращений, потому что на живом стенде гонка неповторяема.
    """
    calls: list[str] = []
    state = {"status": "COMPENSATION_FAILED"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/api/definitions":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "d1", "name": DEFINITION_NAME, "is_active": True},
                    ],
                    "total": 1,
                    "limit": 200,
                    "offset": 0,
                },
            )
        if request.method == "POST" and path == "/api/instances":
            return httpx.Response(201, json={"id": "i1"})
        if path == "/api/instances/i1/compensation/retry":
            state["status"] = "COMPENSATED"
            return httpx.Response(200, json={"status": "COMPENSATING", "message": "повтор"})
        if request.method == "GET" and path == "/api/instances/i1":
            return httpx.Response(200, json={"status": state["status"]})
        if request.method == "GET" and path == "/api/instances/i1/events":
            return httpx.Response(
                200,
                json={
                    "items": [
                        _event("COMPENSATION_STARTED", for_step="charge_payment"),
                        _event("COMPENSATION_COMPLETED", for_step="charge_payment"),
                        _event("COMPENSATION_STARTED", for_step="reserve_inventory"),
                        _event("COMPENSATION_FAILED", for_step="reserve_inventory"),
                        _event("COMPENSATION_STARTED", for_step="reserve_inventory"),
                        _event("COMPENSATION_COMPLETED", for_step="reserve_inventory"),
                    ]
                },
            )
        return httpx.Response(200, json={"active": [], "message": ""})

    async def scenario() -> Any:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as client:
            return await run_act(client, ACTS[2], wait_seconds=5)

    result = asyncio.run(scenario())

    assert result.passed, result.error
    injected = calls.index("POST /api/chaos/fail-compensation")
    started = calls.index("POST /api/instances")
    repeated = calls.index("POST /api/instances/i1/compensation/retry")
    cleared_before = max(
        index
        for index, call in enumerate(calls)
        if call == "DELETE /api/chaos" and index < repeated
    )
    assert injected < started, "правила должны стоять до запуска"
    assert injected < cleared_before < repeated, "причина снимается до повтора"
    assert calls[-1] == "DELETE /api/chaos", "правила снимаются после акта"


def test_foreign_terminal_state_fails_the_act_without_waiting() -> None:
    """Чужой терминальный статус — отказ сразу, а не ожидание таймаута."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/api/definitions":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "d1", "name": DEFINITION_NAME, "is_active": True},
                    ],
                    "total": 1,
                    "limit": 200,
                    "offset": 0,
                },
            )
        if request.method == "POST" and path == "/api/instances":
            return httpx.Response(201, json={"id": "i1"})
        if request.method == "GET" and path == "/api/instances/i1":
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json={"active": [], "message": ""})

    async def scenario() -> Any:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as client:
            return await run_act(client, ACTS[2], wait_seconds=30)

    result = asyncio.run(scenario())

    assert not result.passed
    assert "вместо COMPENSATION_FAILED" in result.error
    assert "DELETE /api/chaos" in calls, "правила снимаются и при отказе"
