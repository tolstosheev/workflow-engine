"""Обработчик для chaos-тестов: держит шаг дольше, чем длится проверка.

Нужен, чтобы воркер гарантированно находился «посреди шага» в момент
проверки. Реальный сетевой вызов здесь не нужен — важна длительность.
"""

from __future__ import annotations

import asyncio
from typing import Any

HOLD_SECONDS = 3.0


async def hold(ctx: Any) -> dict[str, Any]:
    """Имитирует долгий внешний вызов."""
    await asyncio.sleep(HOLD_SECONDS)
    return {"held": True, "seconds": HOLD_SECONDS}
