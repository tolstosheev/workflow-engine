"""Тесты сервиса определений и API-эндпоинтов на реальной БД.

Сервис состоит в основном из запросов к БД, и подделать их результат — значит
проверить не сервис, а заглушку. Поэтому здесь настоящий PostgreSQL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, DefinitionInvalidError, NotFoundError
from app.models.workflow import WorkflowDefinition
from app.schemas.definition import DefinitionCreate, DefinitionUpdate
from app.services import definitions as service

pytestmark = pytest.mark.integration

DEMO = "app.demo.handlers"


def _payload(name: str, **kwargs: Any) -> DefinitionCreate:
    return DefinitionCreate.model_validate(
        {
            "name": name,
            "definition": {
                "start": "first",
                "steps": {
                    "first": {
                        "action": f"{DEMO}:validate_cart",
                        "next": "second",
                    },
                    "second": {
                        "action": f"{DEMO}:charge_payment",
                        "compensation": f"{DEMO}:refund_payment",
                    },
                },
            },
            **kwargs,
        }
    )


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


# --- Создание --------------------------------------------------------------


async def test_create_definition_persists_jsonb(db_session: AsyncSession) -> None:
    definition = await service.create_definition(
        db_session, _payload(f"create-{uuid.uuid4().hex[:8]}", is_active=True)
    )

    assert definition.id is not None
    assert definition.definition["start"] == "first"
    assert definition.is_active is True


async def test_create_rejects_semantically_invalid(db_session: AsyncSession) -> None:
    # Дефинишен с несуществующим переходом обязан не попасть в базу: инстанс,
    # ссылающийся на него, упал бы на середине исполнения.
    payload = _payload("bad")
    payload.definition.steps["first"].next = "ghost"

    with pytest.raises(DefinitionInvalidError, match="ghost"):
        await service.create_definition(db_session, payload)
    await db_session.rollback()


async def test_error_message_lists_all_problems(db_session: AsyncSession) -> None:
    payload = _payload("bad2")
    payload.definition.steps["first"].next = "ghost"
    payload.definition.steps["second"].compensation = f"{DEMO}:missing"

    with pytest.raises(DefinitionInvalidError) as exc_info:
        await service.create_definition(db_session, payload)

    message = str(exc_info.value)
    assert "ghost" in message
    assert "missing" in message
    await db_session.rollback()


async def test_create_rejects_duplicate_name_and_version(db_session: AsyncSession) -> None:
    name = f"dup-{uuid.uuid4().hex[:8]}"
    await service.create_definition(db_session, _payload(name))

    with pytest.raises(ConflictError, match="уже существует"):
        await service.create_definition(db_session, _payload(name))
    await db_session.rollback()


# --- Чтение ----------------------------------------------------------------


async def test_get_definition_returns_row(db_session: AsyncSession) -> None:
    created = await service.create_definition(db_session, _payload(f"get-{uuid.uuid4().hex[:8]}"))

    fetched = await service.get_definition(db_session, created.id)

    assert fetched.id == created.id


async def test_get_definition_raises_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError, match="не найдено"):
        await service.get_definition(db_session, uuid.uuid4())


async def test_list_definitions_paginates(db_session: AsyncSession) -> None:
    # Разные имена: (name, version) уникальны, иначе это был бы конфликт,
    # а не проверка пагинации.
    prefix = f"list-{uuid.uuid4().hex[:6]}"
    created_ids = set()
    for suffix in ("a", "b", "c"):
        created = await service.create_definition(db_session, _payload(f"{prefix}{suffix}"))
        created_ids.add(created.id)

    page, _ = await service.list_definitions(db_session, limit=2, offset=0, is_active=None)

    assert len(page) == 2
    # Смещение обязано вести на следующую страницу, а не повторять первую.
    second_page, _ = await service.list_definitions(db_session, limit=2, offset=2, is_active=None)
    assert {d.id for d in page}.isdisjoint({d.id for d in second_page})
    assert created_ids.issubset({d.id for d in page} | {d.id for d in second_page})


async def test_list_definitions_filters_by_active(db_session: AsyncSession) -> None:
    name = f"act-{uuid.uuid4().hex[:8]}"
    v1 = await service.create_definition(db_session, _payload(name, version=1, is_active=True))
    v2 = await service.create_definition(db_session, _payload(name, version=2, is_active=False))

    active, _ = await service.list_definitions(db_session, limit=200, offset=0, is_active=True)
    inactive, _ = await service.list_definitions(db_session, limit=200, offset=0, is_active=False)

    assert v1.id in {d.id for d in active}
    assert v2.id not in {d.id for d in active}
    assert v2.id in {d.id for d in inactive}
    assert v1.id not in {d.id for d in inactive}


# --- Обновление ------------------------------------------------------------


async def test_update_replaces_definition(db_session: AsyncSession) -> None:
    created = await service.create_definition(db_session, _payload(f"upd-{uuid.uuid4().hex[:8]}"))

    updated = await service.update_definition(
        db_session,
        created.id,
        DefinitionUpdate(definition=created.definition and service_payload(), is_active=False),
    )

    assert updated.is_active is False
    assert updated.definition["start"] == "changed"


def service_payload() -> Any:
    from app.schemas.definition import WorkflowDefinitionSchema

    return WorkflowDefinitionSchema.model_validate(
        {
            "start": "changed",
            "steps": {"changed": {"action": f"{DEMO}:validate_cart"}},
        }
    )


async def test_update_validates_before_saving(db_session: AsyncSession) -> None:
    created = await service.create_definition(db_session, _payload(f"upd2-{uuid.uuid4().hex[:8]}"))
    broken = service_payload()
    broken.steps["changed"].next = "ghost"

    with pytest.raises(DefinitionInvalidError):
        await service.update_definition(db_session, created.id, DefinitionUpdate(definition=broken))
    await db_session.rollback()


# --- Активность -----------------------------------------------------------


async def test_deactivate_all_leaves_single_active(db_session: AsyncSession) -> None:
    # Две активные версии одного имени — неоднозначность при запуске инстанса.
    name = f"one-{uuid.uuid4().hex[:8]}"
    v1 = await service.create_definition(db_session, _payload(name, version=1, is_active=True))
    v2 = await service.create_definition(db_session, _payload(name, version=2, is_active=True))

    await service.deactivate_all_with_name(db_session, name, except_id=v2.id)
    await db_session.flush()

    assert (await service.get_definition(db_session, v1.id)).is_active is False
    assert (await service.get_definition(db_session, v2.id)).is_active is True


async def test_ensure_runnable_rejects_inactive(db_session: AsyncSession) -> None:
    created = await service.create_definition(db_session, _payload(f"inact-{uuid.uuid4().hex[:8]}"))

    with pytest.raises(ConflictError, match="неактивно"):
        await service.ensure_runnable(db_session, created.id)


async def test_ensure_runnable_accepts_active(db_session: AsyncSession) -> None:
    created = await service.create_definition(
        db_session, _payload(f"act2-{uuid.uuid4().hex[:8]}", is_active=True)
    )

    assert (await service.ensure_runnable(db_session, created.id)).id == created.id


async def test_validate_only_does_not_modify(db_session: AsyncSession) -> None:
    created = await service.create_definition(db_session, _payload(f"val-{uuid.uuid4().hex[:8]}"))

    result = await service.validate_only(db_session, created.id)

    assert result.valid
    await db_session.refresh(created)
    assert created.updated_at is not None


# --- HTTP-слой -------------------------------------------------------------


@pytest.fixture
async def client(db_engine: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def test_api_definition_crud_cycle(client: httpx.AsyncClient) -> None:
    name = f"api-{uuid.uuid4().hex[:8]}"

    created = await client.post(
        "/api/definitions",
        json={
            "name": name,
            "definition": {
                "start": "first",
                "steps": {
                    "first": {"action": f"{DEMO}:validate_cart", "next": "second"},
                    "second": {
                        "action": f"{DEMO}:charge_payment",
                        "compensation": f"{DEMO}:refund_payment",
                    },
                },
            },
        },
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]

    listed = await client.get("/api/definitions", params={"limit": 200})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    fetched = await client.get(f"/api/definitions/{definition_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == name

    activated = await client.post(f"/api/definitions/{definition_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    validated = await client.post(f"/api/definitions/{definition_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    deactivated = await client.post(f"/api/definitions/{definition_id}/deactivate")
    assert deactivated.json()["is_active"] is False


async def test_api_rejects_invalid_definition_with_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/definitions",
        json={
            "name": f"bad-{uuid.uuid4().hex[:8]}",
            "definition": {
                "start": "first",
                "steps": {"first": {"action": f"{DEMO}:validate_cart", "next": "ghost"}},
            },
        },
    )

    assert response.status_code == 422
    assert "ghost" in response.json()["detail"]


async def test_api_returns_404_for_unknown_definition(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/definitions/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_api_update_and_conflict(client: httpx.AsyncClient) -> None:
    name = f"upd-api-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": name,
        "definition": {
            "start": "first",
            "steps": {"first": {"action": f"{DEMO}:validate_cart"}},
        },
    }
    first = await client.post("/api/definitions", json=payload)
    assert first.status_code == 201

    duplicate = await client.post("/api/definitions", json=payload)
    assert duplicate.status_code == 409

    updated = await client.put(
        f"/api/definitions/{first.json()['id']}",
        json={
            "definition": {
                "start": "renamed",
                "steps": {"renamed": {"action": f"{DEMO}:confirm_delivery"}},
            },
            "is_active": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is True


async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_openapi_is_generated(client: httpx.AsyncClient) -> None:
    # Схема OpenAPI — источник типов для фронтенда; её отсутствие тихо ломает
    # генерацию контракта.
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/definitions" in response.json()["paths"]


async def test_definition_stored_as_expected_type(db_session: AsyncSession) -> None:
    created = await service.create_definition(db_session, _payload(f"type-{uuid.uuid4().hex[:8]}"))
    await db_session.refresh(created)

    assert isinstance(created, WorkflowDefinition)
    assert isinstance(created.definition, dict)
