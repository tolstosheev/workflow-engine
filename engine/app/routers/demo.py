"""Склад демонстрационной предметной области.

Только чтение. Предметная область показывается, но не меняется из интерфейса:
иначе демонстрация отката лишилась бы смысла — человек «освободил» бы резерв
кнопкой и увидел успех там, где должен увидеть компенсацию.

Эндпоинт появился вместе с ADR-0020: подтверждение доставки возвращает резерв
на склад, и возвращение нужно чем-то показать. Проверкой этого занимается
смоук по снимкам БД — эндпоинт показывает, а не утверждает.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.workflow import DemoInventoryItem

router = APIRouter(prefix="/api/demo", tags=["demo"])


class InventoryItemOut(BaseModel):
    """Одна позиция склада."""

    model_config = {"from_attributes": True}

    sku: str
    available: int
    reserved: int

    @property
    def free(self) -> int:
        """Свободный остаток: то, чем ещё можно резервировать."""
        return self.available - self.reserved


class InventoryOut(BaseModel):
    items: list[InventoryItemOut]


@router.get("/inventory", response_model=InventoryOut)
async def list_inventory(db_session: AsyncSession = Depends(get_db)) -> InventoryOut:
    """Остатки склада, по артикулу.

    Порядок по артикулу, а не по вставке: список читают при разборе заказа, и
    порядок строк должен быть предсказуемым.
    """
    rows = await db_session.scalars(select(DemoInventoryItem).order_by(DemoInventoryItem.sku))
    return InventoryOut(items=[InventoryItemOut.model_validate(row) for row in rows])
