"""Чтение склада демонстрационной области.

Ручки изменения нет и быть не должно: предметная область показывается, чтобы
человек видел, что резерв занял место и вернулся, но не трогается из
интерфейса — иначе демонстрация отката лишилась бы смысла.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import DemoInventoryItem
from scripts.definitions import DEMO_INVENTORY
from tests.integration.test_instances_api import client as _client
from tests.integration.test_instances_api import db_session as db_session

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("inventory")]

# Фикстуры переиспользуются как есть: оборачивать их в свои нельзя — pytest
# считает вызов фикстуры из фикстуры ошибкой, а не наследованием.
client = _client


async def test_inventory_lists_every_sku(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/demo/inventory")).json()

    listed = {item["sku"] for item in body["items"]}

    assert listed == {row["sku"] for row in DEMO_INVENTORY}


async def test_inventory_is_sorted_by_sku(client: httpx.AsyncClient) -> None:
    items = (await client.get("/api/demo/inventory")).json()["items"]

    # Порядок строк читают при разборе заказа: он должен быть предсказуемым,
    # а не зависеть от того, как строки легли на диск.
    skus = [item["sku"] for item in items]

    assert skus == sorted(skus)


async def test_inventory_is_read_only(client: httpx.AsyncClient) -> None:
    # Методы изменения не должны существовать: 405, а не тихий успех.
    response = await client.post("/api/demo/inventory", json={"sku": "SKU-000", "reserved": 0})

    assert response.status_code == 405


async def test_inventory_reports_numbers_not_words(client: httpx.AsyncClient) -> None:
    """Остатки приходят числами: склад считывают глазами, а не пересказывают.

    Значение строкой означало бы, что сравнивать его в интерфейсе нечем, и
    панель показывала бы «0» как текст рядом с числом в соседней колонке.
    """
    items = (await client.get("/api/demo/inventory")).json()["items"]

    for item in items:
        assert isinstance(item["available"], int)
        assert isinstance(item["reserved"], int)
        assert 0 <= item["reserved"] <= item["available"]


async def test_inventory_shows_reservation_while_it_is_held(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Занятый резерв виден через API — ради этого эндпоинт и нужен.

    Подтверждение доставки возвращает товар на склад (ADR-0020), и человек
    должен видеть оба состояния: пока груз едет, резерв занят.
    """
    row = await db_session.scalar(
        select(DemoInventoryItem).where(DemoInventoryItem.sku == DEMO_INVENTORY[0]["sku"])
    )
    assert row is not None
    row.reserved = 5
    await db_session.commit()

    items = (await client.get("/api/demo/inventory")).json()["items"]

    held = next(item for item in items if item["sku"] == DEMO_INVENTORY[0]["sku"])
    assert held["reserved"] == 5
