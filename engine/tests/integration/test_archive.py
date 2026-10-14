"""Архивирование завершённых процессов (ADR-0017).

Уборка списка не удаляет ничего: журнал остаётся целым, состояние
архивного процесса по-прежнему выводится повтором событий. Проверяется именно
это — потому что «просто убрать из выдачи» и «стереть историю» выглядят на
экране одинаково, а разница между ними и есть суть решения.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_instances_api import (
    ORDER_FLOW,
)
from tests.integration.test_instances_api import (
    client as _client,
)
from tests.integration.test_instances_api import (
    db_session as _db_session,
)

# Фикстуры переиспользуются как есть: оборачивать их в свои нельзя — pytest
# считает вызов фикстуры из фикстуры ошибкой, а не наследованием.
client = _client
db_session = _db_session

pytestmark = pytest.mark.integration


async def _definition(client: httpx.AsyncClient) -> dict:
    created = await client.post(
        "/api/definitions",
        json={"name": f"arch-{uuid.uuid4().hex[:8]}", "definition": ORDER_FLOW},
    )
    assert created.status_code == 201, created.text
    activated = await client.post(f"/api/definitions/{created.json()['id']}/activate")
    assert activated.status_code == 200
    return activated.json()


async def _instance(client: httpx.AsyncClient, definition_id: str) -> str:
    response = await client.post(
        "/api/instances", json={"definition_id": definition_id, "input": {"amount": "10.00"}}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _force_status(db_session: AsyncSession, instance_id: str, status: str) -> None:
    await db_session.execute(
        text("UPDATE workflow_instances SET status = :s WHERE id = :i"),
        {"s": status, "i": instance_id},
    )
    await db_session.commit()


# --- Уборка -----------------------------------------------------------------


async def test_archive_hides_finished_from_the_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    definition = await _definition(client)
    finished = await _instance(client, definition["id"])
    await _force_status(db_session, finished, "COMPLETED")

    before = (await client.get("/api/instances?limit=200")).json()
    assert any(item["id"] == finished for item in before["items"])

    archived = await client.post("/api/instances/archive", json={})
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived"] >= 1

    after = (await client.get("/api/instances?limit=200")).json()
    assert all(item["id"] != finished for item in after["items"])
    assert after["archived_total"] >= 1


async def test_archive_keeps_process_awaiting_a_person(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Главная граница решения: PAUSED ждёт решения человека, и спрятать такой
    # процесс — значит спрятать то, о ком человек и должен помнить.
    definition = await _definition(client)
    paused = await _instance(client, definition["id"])
    await _force_status(db_session, paused, "PAUSED")

    await client.post("/api/instances/archive", json={})

    listed = (await client.get("/api/instances?limit=200")).json()
    assert any(item["id"] == paused for item in listed["items"])


async def test_archive_keeps_failed_compensation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # COMPENSATION_FAILED — не конец, а незакрытый инцидент. Прячется он тем
    # же способом, что и пауза, и по той же причине.
    definition = await _definition(client)
    stuck = await _instance(client, definition["id"])
    await _force_status(db_session, stuck, "COMPENSATION_FAILED")

    await client.post("/api/instances/archive", json={})

    listed = (await client.get("/api/instances?limit=200")).json()
    assert any(item["id"] == stuck for item in listed["items"])


# --- Целостность ------------------------------------------------------------


async def test_archive_does_not_touch_the_journal(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Если бы уборка удаляла строки, схема отклонила бы удаление инстанса с
    # событиями: `ondelete="RESTRICT"` на журнале. Здесь проверяется главное —
    # что ни журнал, ни выведенное из него состояние не изменились.
    definition = await _definition(client)
    finished = await _instance(client, definition["id"])
    await _force_status(db_session, finished, "COMPLETED")

    events_before = (await client.get(f"/api/instances/{finished}/events?limit=100")).json()
    state_before = (await client.get(f"/api/instances/{finished}/state")).json()

    await client.post("/api/instances/archive", json={})

    events_after = (await client.get(f"/api/instances/{finished}/events?limit=100")).json()
    state_after = (await client.get(f"/api/instances/{finished}/state")).json()

    assert events_after["total"] == events_before["total"]
    assert events_after["total"] > 0
    assert state_after == state_before

    # И побочно: состояние выводится из журнала, а не из колонки. Колонку здесь
    # меняли напрямую, минуя событие, — и разбор этого не учёл. Именно поэтому
    # уборка по колонке безопасна: в живой системе колонка всегда пишется
    # вместе с событием в одной транзакции (§3.1).
    assert state_after["status"] == "PENDING"


async def test_archived_process_is_still_readable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    definition = await _definition(client)
    finished = await _instance(client, definition["id"])
    await _force_status(db_session, finished, "COMPLETED")
    await client.post("/api/instances/archive", json={})

    page = await client.get(f"/api/instances/{finished}")
    assert page.status_code == 200, page.text
    assert page.json()["archived_at"] is not None


# --- Возврат ----------------------------------------------------------------


async def test_unarchive_returns_the_process_to_the_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    definition = await _definition(client)
    finished = await _instance(client, definition["id"])
    await _force_status(db_session, finished, "COMPLETED")
    await client.post("/api/instances/archive", json={})

    restored = await client.post(f"/api/instances/{finished}/unarchive")
    assert restored.status_code == 200, restored.text

    listed = (await client.get("/api/instances?limit=200")).json()
    assert any(item["id"] == finished for item in listed["items"])
    assert (await client.get(f"/api/instances/{finished}")).json()["archived_at"] is None


async def test_unarchive_of_a_live_process_is_not_found(client: httpx.AsyncClient) -> None:
    # Процесс, который никогда не архивировали, не «возвращается из архива»:
    # иначе 404 означал бы «нет такого инстанса», а на деле он есть и жив.
    definition = await _definition(client)
    live = await _instance(client, definition["id"])

    response = await client.post(f"/api/instances/{live}/unarchive")

    assert response.status_code == 404, response.text


async def test_archive_only_can_be_called_twice_without_double_count(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Повторная уборка не должна считать уже архивные: иначе кнопка в
    # интерфейсе показывала бы число больше, чем убрала.
    definition = await _definition(client)
    finished = await _instance(client, definition["id"])
    await _force_status(db_session, finished, "COMPLETED")

    await client.post("/api/instances/archive", json={})
    second = await client.post("/api/instances/archive", json={})

    assert second.status_code == 200
    assert second.json()["archived"] == 0
