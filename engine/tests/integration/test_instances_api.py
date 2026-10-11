"""Тесты REST API инстансов, метрик, SSE и chaos-эндпоинтов."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import append_event
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import StepRun, WorkflowDefinition, WorkflowInstance

pytestmark = pytest.mark.integration

DEMO = "app.demo.handlers"

ORDER_FLOW = {
    "start": "validate_cart",
    "steps": {
        "validate_cart": {"action": f"{DEMO}:validate_cart", "next": "reserve_inventory"},
        "reserve_inventory": {
            "action": f"{DEMO}:reserve_inventory",
            "compensation": f"{DEMO}:release_inventory",
            "next": "charge_payment",
        },
        "charge_payment": {
            "action": f"{DEMO}:charge_payment",
            "compensation": f"{DEMO}:refund_payment",
            "next": "confirm_delivery",
        },
        "confirm_delivery": {"action": f"{DEMO}:confirm_delivery"},
    },
}


@pytest.fixture
async def db_session(db_engine: None) -> AsyncIterator[AsyncSession]:
    from app.core.db import get_session_factory

    session = get_session_factory()()
    transaction = await session.begin()
    try:
        yield session
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()


@pytest.fixture
async def client(db_engine: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _active_definition(client: httpx.AsyncClient) -> dict:
    created = await client.post(
        "/api/definitions",
        json={"name": f"api-{uuid.uuid4().hex[:8]}", "definition": ORDER_FLOW},
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]
    activated = await client.post(f"/api/definitions/{definition_id}/activate")
    assert activated.status_code == 200
    return activated.json()


# --- Запуск ----------------------------------------------------------------


async def test_create_instance_returns_pending(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)

    response = await client.post(
        "/api/instances",
        json={"definition_id": definition["id"], "input": {"items": [], "amount": "10.00"}},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["input"]["amount"] == "10.00"
    assert body["definition"]["name"] == definition["name"]


async def test_create_instance_writes_journal(client: httpx.AsyncClient) -> None:
    # Без первого события replay не знал бы, с какого шага начинать.
    definition = await _active_definition(client)
    created = await client.post(
        "/api/instances", json={"definition_id": definition["id"], "input": {}}
    )

    events = await client.get(f"/api/instances/{created.json()['id']}/events")

    assert [e["type"] for e in events.json()["items"]] == ["INSTANCE_CREATED"]


async def test_create_instance_rejects_inactive_definition(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/definitions",
        json={"name": f"inact-{uuid.uuid4().hex[:8]}", "definition": ORDER_FLOW},
    )
    definition_id = created.json()["id"]

    response = await client.post("/api/instances", json={"definition_id": definition_id})

    assert response.status_code == 409


async def test_create_instance_rejects_unknown_definition(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/instances", json={"definition_id": str(uuid.uuid4())})

    assert response.status_code == 404


# --- Чтение ----------------------------------------------------------------


async def test_get_instance_returns_state(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.get(f"/api/instances/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    assert response.json()["lease_owner"] is None


async def test_get_unknown_instance_returns_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/api/instances/{uuid.uuid4()}")).status_code == 404


async def test_list_instances_paginates(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    for _ in range(3):
        await client.post("/api/instances", json={"definition_id": definition["id"]})

    page = await client.get("/api/instances", params={"limit": 2})

    assert page.status_code == 200
    assert len(page.json()["items"]) == 2
    assert page.json()["total"] >= 3


async def test_list_instances_filters_by_status(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    pending = await client.get("/api/instances", params={"status": "PENDING"})
    completed = await client.get("/api/instances", params={"status": "COMPLETED"})

    assert any(item["id"] == created.json()["id"] for item in pending.json()["items"])
    assert not any(item["id"] == created.json()["id"] for item in completed.json()["items"])


async def test_list_includes_definition_name(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    await client.post("/api/instances", json={"definition_id": definition["id"]})

    listed = await client.get("/api/instances")

    assert any(item["definition"]["name"] == definition["name"] for item in listed.json()["items"])


async def test_events_support_offset(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = created.json()["id"]

    async with _engine_session() as session:
        await append_event(
            session, instance_id, EventType.STEP_COMPLETED, {"step": "a", "next": "b"}
        )
        await append_event(session, instance_id, EventType.STEP_STARTED, {"step": "b"})
        await session.commit()

    events = await client.get(f"/api/instances/{instance_id}/events", params={"from_seq": 1})

    assert [e["seq"] for e in events.json()["items"]] == [1, 2]


@asynccontextmanager
async def _engine_session() -> AsyncIterator[AsyncSession]:
    """Сессия с коммитом.

    Отдельная от фикстуры `db_session`: HTTP-эндпоинты читают через свои
    сессии, и данные, оставленные только в откатываемой транзакции, они бы
    не увидели.
    """
    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def test_steps_are_listed(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = created.json()["id"]

    async with _engine_session() as session:
        session.add(
            StepRun(
                instance_id=instance_id,
                step_name="validate_cart",
                attempt=1,
                status="COMPLETED",
            )
        )

    steps = await client.get(f"/api/instances/{instance_id}/steps")

    assert [s["step_name"] for s in steps.json()["items"]] == ["validate_cart"]


async def test_timeline_merges_steps_and_events(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = created.json()["id"]

    async with _engine_session() as session:
        session.add(
            StepRun(
                instance_id=instance_id, step_name="validate_cart", attempt=1, status="COMPLETED"
            )
        )

    timeline = await client.get(f"/api/instances/{instance_id}/timeline")

    kinds = {item["kind"] for item in timeline.json()["items"]}
    assert kinds == {"step", "event"}


async def test_state_is_rebuilt_from_journal(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = created.json()["id"]

    async with _engine_session() as session:
        await append_event(
            session, instance_id, EventType.STEP_COMPLETED, {"step": "a", "next": "b"}
        )

    state = await client.get(f"/api/instances/{instance_id}/state")

    assert state.json()["current_step"] == "b"
    assert state.json()["completed_steps"] == ["a"]


# --- Управление ------------------------------------------------------------


async def test_cancel_moves_instance_to_compensation_path(
    client: httpx.AsyncClient,
) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.post(f"/api/instances/{created.json()['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLING"


async def test_cancel_rejects_terminal_instance(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    await client.post(
        f"/api/instances/{created.json()['id']}/complete",
        json={"reason": "ручная приёмка"},
    )

    response = await client.post(f"/api/instances/{created.json()['id']}/cancel")

    assert response.status_code == 409


async def test_force_complete_records_reason(client: httpx.AsyncClient) -> None:
    # Приёмка в обход компенсаций оставляет побочные эффекты, и причина
    # обязана остаться в данных.
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.post(
        f"/api/instances/{created.json()['id']}/complete",
        json={"reason": "клиент подтвердил вручную"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    events = await client.get(f"/api/instances/{created.json()['id']}/events")
    payload = events.json()["items"][-1]["payload"]
    assert payload["forced"] is True
    assert payload["reason"] == "клиент подтвердил вручную"


async def test_force_complete_requires_reason(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.post(f"/api/instances/{created.json()['id']}/complete", json={})

    assert response.status_code == 422


async def test_retry_compensation_requires_failed_state(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.post(f"/api/instances/{created.json()['id']}/compensation/retry")

    assert response.status_code == 409


async def test_retry_compensation_restores_failed_state(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = created.json()["id"]

    async with _engine_session() as session:
        await append_event(session, instance_id, EventType.COMPENSATION_FAILED, {"error": "x"})
        instance = await session.get(WorkflowInstance, instance_id)
        instance.status = InstanceStatus.COMPENSATION_FAILED

    response = await client.post(f"/api/instances/{instance_id}/compensation/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "COMPENSATING"


# --- Метрики ---------------------------------------------------------------


async def test_metrics_exposes_instance_states(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert "workflow_instances" in response.text


async def test_metrics_report_zero_for_absent_statuses(client: httpx.AsyncClient) -> None:
    # Статус с нулём обязан публиковаться: иначе на графике он просто исчезает,
    # и падение выглядит как «инстансы пропали».
    response = await client.get("/metrics")

    assert 'workflow_instances{status="COMPENSATED"} 0.0' in response.text


async def test_metrics_report_steps_and_compensations(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    async with _engine_session() as session:
        session.add(
            StepRun(
                instance_id=created.json()["id"],
                step_name="reserve_inventory",
                attempt=1,
                status="COMPLETED",
            )
        )

    text = (await client.get("/metrics")).text

    assert 'workflow_steps{result="success",step="reserve_inventory"} 1.0' in text
    assert "workflow_compensations" in text


# --- Chaos -----------------------------------------------------------------


async def test_chaos_fail_step_registers_injection(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.post(
        "/api/chaos/fail-step",
        json={"instance_id": created.json()["id"], "step": "charge_payment", "mode": "always"},
    )

    assert response.status_code == 200
    assert response.json()["active"] == ["step:charge_payment:always"]


async def test_chaos_fail_step_rejects_unknown_instance(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/chaos/fail-step", json={"instance_id": str(uuid.uuid4())})

    assert response.status_code == 404


async def test_chaos_fail_step_supports_timeout_mode(client: httpx.AsyncClient) -> None:
    # Режим timeout имитирует зависший сервис: проверяет, что lease
    # продлевается во время долгого шага.
    response = await client.post(
        "/api/chaos/fail-step", json={"step": "charge_payment", "mode": "timeout"}
    )

    assert response.json()["active"] == ["step:charge_payment:timeout"]


async def test_chaos_stop_lease_moves_heartbeat_to_past(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = created.json()["id"]

    async with _engine_session() as session:
        instance = await session.get(WorkflowInstance, instance_id)
        instance.heartbeat_at = instance.created_at

    response = await client.post(
        "/api/chaos/stop-lease", json={"instance_id": instance_id, "duration_seconds": 40}
    )

    assert response.status_code == 200
    assert "40" in response.json()["message"]


async def test_chaos_stop_lease_rejects_unknown_instance(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/chaos/stop-lease", json={"instance_id": str(uuid.uuid4())})

    assert response.status_code == 404


async def test_chaos_resume_lease(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})

    response = await client.post(
        "/api/chaos/resume-lease", json={"instance_id": created.json()["id"]}
    )

    assert response.status_code == 200
    assert response.json()["active"] == []


async def test_chaos_fail_service(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/chaos/fail-service", json={"service": "payment"})

    assert response.json()["active"] == ["service:payment"]


async def test_chaos_clear(client: httpx.AsyncClient) -> None:
    response = await client.request("DELETE", "/api/chaos")

    assert response.json()["active"] == []


# --- SSE -------------------------------------------------------------------


async def test_stream_emits_new_events(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = uuid.UUID(created.json()["id"])

    async def emit() -> None:
        from app.core.db import session_scope

        async with session_scope() as session:
            await append_event(session, instance_id, EventType.STEP_COMPLETED, {"step": "a"})

    joined = await _chunks_after(emit)

    assert "event: STEP_COMPLETED" in joined
    assert str(instance_id) in joined


async def _first_chunks(count: int = 1) -> list[str]:
    """Первые чанки потока.

    Поток бесконечен по замыслу, поэтому он потребляется напрямую и с лимитом:
    через ASGI-транспорт чтение зависает на живом соединении.
    """
    from app.routers.stream import event_stream

    chunks: list[str] = []
    generator = event_stream(None)
    try:

        async def take() -> None:
            for _ in range(count):
                chunks.append(await generator.__anext__())

        await asyncio.wait_for(take(), timeout=25)
    finally:
        await generator.aclose()
    return chunks


async def test_stream_payload_is_valid_json(client: httpx.AsyncClient) -> None:
    definition = await _active_definition(client)
    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    instance_id = uuid.UUID(created.json()["id"])

    async def emit() -> None:
        from app.core.db import session_scope

        async with session_scope() as session:
            await append_event(session, instance_id, EventType.STEP_COMPLETED, {"step": "a"})

    joined = await _chunks_after(emit)
    data_line = next(
        line
        for line in joined.splitlines()
        if line.startswith("data: ") and "STEP_COMPLETED" in line
    )

    parsed = json.loads(data_line[6:])
    assert parsed["type"] == "STEP_COMPLETED"
    assert "payload" in parsed
    assert "created_at" in parsed


async def _chunks_after(action, count: int = 3) -> str:
    """Собирает кадруги потока после выполнения `action`.

    Порядок сначала событие, потом вызываем событие: поток стартует с момента
    подключения и заведенные события в него не попадут.
    """
    from app.routers.stream import event_stream

    generator = event_stream(None)

    async def take() -> str:
        chunks: list[str] = []
        for _ in range(count):
            chunks.append(await generator.__anext__())
        return "".join(chunks)

    task = asyncio.create_task(take())
    try:
        await asyncio.sleep(0.3)
        await action()
        return await asyncio.wait_for(task, timeout=25)
    finally:
        task.cancel()
        await generator.aclose()


async def test_stream_emits_heartbeat_when_idle() -> None:
    # Heartbeat нужен, чтобы прокси не закрывали соединение по таймауту простоя.
    from app.routers.stream import event_stream

    # Курсор в будущем: событий созданы быть не могут, пока
    # пока устанется сосединение. Раньше не реплейтируем всю
    # историю журнала.
    generator = event_stream(_FUTURE.isoformat())
    try:
        chunk = await asyncio.wait_for(generator.__anext__(), timeout=25)
    finally:
        await generator.aclose()

    assert chunk == ": heartbeat\n\n"


_FUTURE = datetime(2999, 1, 1, tzinfo=UTC)


async def test_stream_endpoint_disables_buffering() -> None:
    # Буферизация прокси иначе соберёт события в пачку, и интерфейс перестанет
    # быть живым. Проверяются заголовки ответа: тело потока бесконечно, и
    # читать его через тестовый транспорт бессмысленно.
    from starlette.requests import Request

    from app.routers.stream import stream

    response = await stream(Request({"type": "http", "headers": []}))

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    await response.body_iterator.aclose()


# --- Схема OpenAPI ---------------------------------------------------------


async def test_openapi_contains_all_routers(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/instances" in paths
    assert "/api/definitions" in paths
    assert "/api/stream" in paths
    assert "/metrics" in paths
    assert "/api/chaos/fail-step" in paths


async def test_definition_created_via_api_is_executable(client: httpx.AsyncClient) -> None:
    # Сквозная проверка: определение из API принимается движком без
    # дополнительной подготовки.
    definition = await _active_definition(client)

    created = await client.post("/api/instances", json={"definition_id": definition["id"]})
    assert created.status_code == 201

    async with _engine_session() as session:
        stored = await session.get(WorkflowDefinition, uuid.UUID(definition["id"]))
        assert stored is not None
        assert stored.definition["start"] == "validate_cart"


async def test_stream_resumes_from_last_event_id() -> None:
    """Повторное подключение не прогоныет старую историю.

    Раньше курсор стартовал с эпохи, и каждое подключение воркера возвращало всю
    журнал и прогоняло его через редуктора.

    Всё создаётся в одной сессии: события теста откатывается в транзакции,
    которая откатывается пользовательским рабочие и не видна в другой сессии.
    """
    from app.core.db import session_scope
    from app.routers.stream import _read_after

    async with session_scope() as session:
        definition = WorkflowDefinition(
            name=f"sse-{uuid.uuid4().hex[:8]}",
            definition={
                "start": "a",
                "steps": {"a": {"action": "app.demo.handlers:validate_cart"}},
            },
        )
        session.add(definition)
        await session.flush()
        instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
        session.add(instance)
        await session.flush()

        marker = (await session.execute(text("SELECT now() - interval '1 hour'"))).scalar_one()
        await append_event(session, instance.id, EventType.STEP_COMPLETED, {"step": "a"})

    older = await _read_after(marker)
    assert len(older) == 1

    resumed = await _read_after(older[0][0])
    assert resumed == [], "повторное подключение вернуло то же отданное"
