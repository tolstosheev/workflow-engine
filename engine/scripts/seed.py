"""Наполнение БД демонстрационными данными.

Идемпотентно: повторный запуск обновляет существующие строки, а не падает на
уникальном индексе. Скрипт должен быть безопасен в повторном вызове из
Makefile и из ручной строки — иначе «перезапустить демо» требовало бы сброса
базы, а это единственный способ потерять журнал событий.

Запуск: `python -m scripts.seed`
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import dispose_engine, session_scope
from app.models.workflow import DemoInventoryItem, WorkflowDefinition
from app.schemas.api import ValidationIssue
from app.schemas.definition import DefinitionCreate, DefinitionUpdate, WorkflowDefinitionSchema
from app.services import definitions as definition_service
from app.services.definition_validation import validate_definition
from scripts.definitions import (
    DEFINITION_NAME,
    DEMO_INVENTORY,
    DEMO_VERSION,
    order_fulfillment,
)


async def seed_definition(wait_before_seconds: float) -> tuple[WorkflowDefinition, str]:
    """Создаёт или обновляет демонстрационное определение и активирует его."""
    body = WorkflowDefinitionSchema.model_validate(order_fulfillment(wait_before_seconds))

    async with session_scope() as session:
        existing = await session.scalar(
            select(WorkflowDefinition).where(
                WorkflowDefinition.name == DEFINITION_NAME,
                WorkflowDefinition.version == DEMO_VERSION,
            )
        )

        if existing is None:
            created = await definition_service.create_definition(
                session,
                DefinitionCreate(
                    name=DEFINITION_NAME,
                    version=DEMO_VERSION,
                    definition=body,
                    is_active=True,
                ),
            )
            action = "создано"
        else:
            # Обновление, а не пропуск: иначе правка дефинишена в коде никогда
            # не попадала бы в уже наполненную базу.
            created = await definition_service.update_definition(
                session,
                existing.id,
                DefinitionUpdate(definition=body, is_active=True),
            )
            action = "обновлено"

        # Остальные версии того же имени выключаются: активных должно быть
        # ровно одно, иначе запуск по имени стал бы неоднозначным.
        await definition_service.deactivate_all_with_name(
            session, DEFINITION_NAME, except_id=created.id
        )

        return created, action


async def seed_inventory() -> int:
    """Загружает остатки склада, обновляя существующие SKU."""
    async with session_scope() as session:
        for item in DEMO_INVENTORY:
            statement = (
                insert(DemoInventoryItem)
                .values(**item)
                .on_conflict_do_update(
                    index_elements=[DemoInventoryItem.sku],
                    set_={"available": item["available"]},
                )
            )
            await session.execute(statement)
        return len(DEMO_INVENTORY)


def _report(issues: list[ValidationIssue]) -> None:
    for issue in issues:
        where = f" [{issue.step}]" if issue.step else ""
        print(f"  - {issue.code}{where}: {issue.message}", file=sys.stderr)


async def main_async(wait_before_seconds: float) -> int:
    definition, action = await seed_definition(wait_before_seconds)

    result = validate_definition(WorkflowDefinitionSchema.model_validate(definition.definition))
    if not result.valid:
        print("дефинишен не прошёл валидацию:", file=sys.stderr)
        _report(result.errors)
        return 1
    if result.warnings:
        print("предупреждения валидации:")
        _report(result.warnings)

    inventory_count = await seed_inventory()

    print(f"определение {definition.name!r} v{definition.version} ({action}): id={definition.id}")
    print(f"  шагов: {len(definition.definition.get('steps', {}))}")
    print(f"  ожидание доставки: {wait_before_seconds} с")
    print(f"  склад: {inventory_count} SKU")
    print("\nготово: запустите инстанс через интерфейс или POST /api/instances")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Наполнение БД демонстрационными данными")
    parser.add_argument(
        "--wait",
        type=float,
        default=0,
        help="ожидание перед подтверждением доставки, секунд (по умолчанию 0)",
    )
    args = parser.parse_args()

    async def run() -> int:
        # Пул закрывается внутри того же цикла событий, в котором жил: движок
        # asyncpg привязан к loop, и закрытие после выхода из asyncio.run()
        # привело бы к «Event loop is closed» на выходе.
        try:
            return await main_async(args.wait)
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
