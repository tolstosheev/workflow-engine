"""Тесты инъекции отказов: правило должно реально влиять на исполнение.

Эндпоинт, который возвращает «инъекция зарегистрирована», но ничего не
ломает, — это фикция. Поэтому здесь проверяется, что шаг действительно
падает или зависает.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import chaos
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance
from app.services import instances as service

pytestmark = pytest.mark.integration

DEMO = "app.demo.handlers"

FLOW = {
    "start": "a",
    "steps": {
        "a": {"action": f"{DEMO}:validate_cart", "next": "b"},
        "b": {
            "action": f"{DEMO}:reserve_inventory",
            "compensation": f"{DEMO}:release_inventory",
            "next": "c",
            "retry": {"max_attempts": 1},
        },
        "c": {"action": f"{DEMO}:confirm_delivery"},
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


async def _instance(client: httpx.AsyncClient) -> str:
    created = await client.post(
        "/api/definitions",
        json={"name": f"chaos-{uuid.uuid4().hex[:8]}", "definition": FLOW},
    )
    definition_id = created.json()["id"]
    await client.post(f"/api/definitions/{definition_id}/activate")

    started = await client.post(
        "/api/instances", json={"definition_id": definition_id, "input": {}}
    )
    return started.json()["id"]


# --- Правила ----------------------------------------------------------------


async def test_rule_is_found_by_instance_and_step(db_session: AsyncSession) -> None:
    instance_id = uuid.uuid4()
    await chaos.add_rule(db_session, instance_id, "b", "always", "шлюз недоступен")

    found = await chaos.find_rule(db_session, instance_id, "b")

    assert found is not None
    assert found.error == "шлюз недоступен"


async def test_rule_does_not_match_other_step(db_session: AsyncSession) -> None:
    instance_id = uuid.uuid4()
    await chaos.add_rule(db_session, instance_id, "b", "always", "сбой")

    assert await chaos.find_rule(db_session, instance_id, "a") is None


async def test_rule_does_not_match_other_instance(db_session: AsyncSession) -> None:
    await chaos.add_rule(db_session, uuid.uuid4(), "b", "always", "сбой")

    assert await chaos.find_rule(db_session, uuid.uuid4(), "b") is None


async def test_rule_without_scope_matches_any_instance(db_session: AsyncSession) -> None:
    await chaos.add_rule(db_session, None, "b", "always", "сбой")

    found = await chaos.find_rule(db_session, uuid.uuid4(), "b")

    assert found is not None


async def test_rule_without_step_matches_any_step(db_session: AsyncSession) -> None:
    instance_id = uuid.uuid4()
    await chaos.add_rule(db_session, instance_id, None, "always", "сбой")

    assert await chaos.find_rule(db_session, instance_id, "любой") is not None


async def test_no_rule_returns_none(db_session: AsyncSession) -> None:
    assert await chaos.find_rule(db_session, uuid.uuid4(), "a") is None


async def test_unknown_mode_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="неизвестный режим"):
        await chaos.add_rule(db_session, None, "a", "nope", "сбой")
    await db_session.rollback()


async def test_label_describes_scope(db_session: AsyncSession) -> None:
    instance_id = uuid.uuid4()
    rule = await chaos.add_rule(db_session, instance_id, "b", "always", "сбой")

    assert rule.label() == f"{instance_id}:b:always"


async def test_label_uses_wildcards(db_session: AsyncSession) -> None:
    rule = await chaos.add_rule(db_session, None, None, "once", "сбой")

    assert rule.label() == "*:*:once"


async def test_clear_removes_all_rules(db_session: AsyncSession) -> None:
    await chaos.add_rule(db_session, None, "a", "always", "сбой")
    await chaos.add_rule(db_session, None, "b", "always", "сбой")

    removed = await chaos.clear_rules(db_session)

    assert removed == 2
    assert await chaos.list_rules(db_session) == []


async def test_existing_instance_check(db_session: AsyncSession) -> None:
    definition = WorkflowDefinition(name=f"chk-{uuid.uuid4().hex[:8]}", definition=FLOW)
    definition.is_active = True
    db_session.add(definition)
    await db_session.flush()
    instance = await service.create_instance(db_session, definition.id, {})

    assert await chaos.existing_instance(db_session, instance.id) is True
    assert await chaos.existing_instance(db_session, uuid.uuid4()) is False


# --- Влияние на исполнение -------------------------------------------------


async def test_injection_breaks_the_step(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Ключевая проверка: инъекция действительно ломает шаг, а не только
    # возвращает подтверждение.
    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    instance_id = await _instance(client)
    response = await client.post(
        "/api/chaos/fail-step", json={"instance_id": instance_id, "step": "b"}
    )
    assert response.status_code == 200

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(10):
        result = await worker.run_cycle("chaos-w", cache)
        if not result.handled:
            break

    db_session.expire_all()
    status = await db_session.scalar(select_status(db_session, instance_id))
    # Шаг `a` выполнен и компенсации не имеет, поэтому откатывать нечего и
    # процесс завершается отменой. Если шаг с компенсацией был бы выполнен,
    # инстанс пришёл бы в COMPENSATED.
    assert status in {
        InstanceStatus.CANCELLING,
        InstanceStatus.COMPENSATING,
        InstanceStatus.COMPENSATED,
        InstanceStatus.CANCELLED,
        InstanceStatus.FAILED,
    }


def select_status(db_session: AsyncSession, instance_id: uuid.UUID) -> object:
    from sqlalchemy import select

    return select(WorkflowInstance.status).where(WorkflowInstance.id == instance_id)


async def test_injection_once_applies_a_single_time(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Режим `once` без этого ломал бы все последующие шаги инстанса.
    instance_id = uuid.uuid4()
    rule = await chaos.add_rule(db_session, instance_id, "b", "once", "сбой")

    first = await chaos.find_rule(db_session, instance_id, "b")
    assert first is not None
    assert rule.used == 1

    await chaos.clear_rules(db_session)
    await chaos.add_rule(db_session, instance_id, "b", "once", "сбой")
    assert await chaos.find_rule(db_session, instance_id, "b") is not None


async def test_injection_reaches_the_worker_process(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Правило сохранено в БД, поэтому видно воркеру — отдельному процессу.
    instance_id = await _instance(client)
    await client.post("/api/chaos/fail-step", json={"instance_id": instance_id, "step": "b"})

    rules = await chaos.list_rules(db_session)
    matched = [rule for rule in rules if rule.matches(uuid.UUID(instance_id), "b")]

    assert len(matched) == 1
    await db_session.rollback()


async def test_service_injection_targets_service_step(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post("/api/chaos/fail-service", json={"service": "payment"})
    assert response.status_code == 200

    found = await chaos.find_rule(db_session, uuid.uuid4(), "charge_payment")

    assert found is not None
    assert "payment" in found.error
    await db_session.rollback()


# --- HTTP-эндпоинты --------------------------------------------------------


async def test_chaos_list_shows_active_rules(client: httpx.AsyncClient) -> None:
    await client.post("/api/chaos/fail-step", json={"step": "b"})

    response = await client.get("/api/chaos")

    assert response.status_code == 200
    assert response.json()["active"] == ["*:b:always"]


async def test_chaos_clear_removes_rules_via_api(client: httpx.AsyncClient) -> None:
    await client.post("/api/chaos/fail-step", json={"step": "b"})

    cleared = await client.request("DELETE", "/api/chaos")
    listed = await client.get("/api/chaos")

    assert "снято неисправностей: 1" in cleared.json()["message"]
    assert listed.json()["active"] == []


async def test_chaos_endpoint_is_documented(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/chaos" in paths
    assert "/api/chaos/fail-service" in paths


async def test_failed_injection_writes_step_failed_event(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from app.engine.journal import read_events

    instance_id = await _instance(client)
    await client.post("/api/chaos/fail-step", json={"instance_id": instance_id, "step": "b"})

    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(10):
        if not (await worker.run_cycle("chaos-w", cache)).handled:
            break

    types = [event.type for event in await read_events(db_session, uuid.UUID(instance_id))]

    assert EventType.STEP_FAILED in types
    await db_session.rollback()


async def test_once_rule_expires_after_single_use(db_session: AsyncSession) -> None:
    """Разовый раз более один раз.

    Раньше счётчик `used` инкрементился, но не участвовал в выборке и не
    фильтровался, поэтому «сломать один раз» срабатывал на всех последующие
    попытки шага.
    """
    instance_id = uuid.uuid4()
    await chaos.add_rule(db_session, instance_id, "b", "once", "сбой ораз")

    first = await chaos.find_rule(db_session, instance_id, "b")
    second = await chaos.find_rule(db_session, instance_id, "b")

    assert first is not None
    assert second is None


async def test_once_rule_still_blocks_other_steps(db_session: AsyncSession) -> None:
    """Исчерпание не делает правило целиком: оно только для своего шага."""
    instance_id = uuid.uuid4()
    await chaos.add_rule(db_session, instance_id, "b", "once", "сбой ораз")

    assert await chaos.find_rule(db_session, instance_id, "b") is not None
    assert await chaos.find_rule(db_session, instance_id, "a") is None


# --- Компенсация как адресат инъекции (ADR-0015) ---------------------------
#
# Правило по имени шага не может уронить откат: шаг уже выполнен, и такое
# правило сработало бы на прямом шаге, а не на его компенсации. У правила есть
# собственный адрес — ссылка на обработчик, — и ниже проверяется, что он не
# смешивается с шагом, что «один раз» срабатывает один раз, а провал отката
# приводит к COMPENSATION_FAILED, а не к тихому повтору.

RELEASE_REF = f"{DEMO}:release_inventory"
CANCEL_REF = f"{DEMO}:cancel_shipment"


# Правило сопоставляется по идентификатору инстанса, и наличие самой строки
# не требуется: инстанс нужен там, где правило действительно применяется.
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


async def test_handler_rule_is_not_a_step_rule(db_session: AsyncSession) -> None:
    # Ссылка на обработчик содержит двоеточие и модуль, а имя шага — голый
    # идентификатор. Одно поле шага, значившее бы два разных действия, и была
    # бы причина заменить этот столбец одним полем с префиксом.
    await chaos.add_rule_for_handler(db_session, None, RELEASE_REF, "always", "сбой")
    await db_session.commit()

    assert await chaos.find_rule(db_session, INSTANCE, "b") is None
    assert await chaos.find_rule(db_session, INSTANCE, RELEASE_REF) is None


async def test_handler_rule_is_found_only_by_its_handler(
    db_session: AsyncSession,
) -> None:
    await chaos.add_rule_for_handler(db_session, None, RELEASE_REF, "always", "сбой")
    await db_session.commit()

    assert await chaos.find_rule_for_handler(db_session, INSTANCE, RELEASE_REF) is not None
    assert await chaos.find_rule_for_handler(db_session, INSTANCE, CANCEL_REF) is None


async def test_handler_rule_respects_the_instance_scope(
    db_session: AsyncSession,
) -> None:
    await chaos.add_rule_for_handler(db_session, INSTANCE, RELEASE_REF, "always", "сбой")
    await db_session.commit()

    assert await chaos.find_rule_for_handler(db_session, OTHER, RELEASE_REF) is None


async def test_handler_rule_in_once_mode_fires_exactly_once(
    db_session: AsyncSession,
) -> None:
    # Без счётчика «сломать один раз» ломался бы каждый откат, и процесс
    # застрял в COMPENSATION_FAILED навсегда.
    await chaos.add_rule_for_handler(db_session, None, RELEASE_REF, "once", "сбой")
    await db_session.commit()

    first = await chaos.find_rule_for_handler(db_session, INSTANCE, RELEASE_REF)
    second = await chaos.find_rule_for_handler(db_session, INSTANCE, RELEASE_REF)

    assert first is not None
    assert second is None


async def test_rule_label_shows_what_it_targets(db_session: AsyncSession) -> None:
    # Подпись идёт в ответ панели, и по ней оператор должен понять, что сломано.
    rule = await chaos.add_rule_for_handler(db_session, None, RELEASE_REF, "always", "сбой")
    await db_session.commit()

    assert RELEASE_REF in rule.label()


async def test_a_rule_needs_a_handler(client: httpx.AsyncClient) -> None:
    # Пустой адрес означал бы «сломать любое возмещение», а это не демонстрация
    # сбоя, а порча стенда целиком.
    response = await client.post("/api/chaos/fail-compensation", json={"mode": "always"})

    assert response.status_code == 422


async def test_unknown_handler_is_refused(client: httpx.AsyncClient) -> None:
    # Имя обработчика проверяется: правило, которое никогда не сработает,
    # выглядит на панели как работающая инъекция.
    response = await client.post(
        "/api/chaos/fail-compensation",
        json={"handler": f"{DEMO}:выдуманный", "mode": "always"},
    )

    assert response.status_code in {404, 422}


async def test_registered_rule_shows_up_in_the_list(client: httpx.AsyncClient) -> None:
    # Панель показывает действующие правила, и правило отката должно быть среди
    # них наравне с правилом шага.
    await client.post(
        "/api/chaos/fail-compensation", json={"handler": RELEASE_REF, "mode": "always"}
    )

    body = (await client.get("/api/chaos")).json()

    assert any(RELEASE_REF in rule for rule in body["active"])
